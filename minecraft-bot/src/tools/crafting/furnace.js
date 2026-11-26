const { object, text, number, position, describe } = require('../schema');
const { ensure, items, count, state } = require('../common');
const { interact } = require('../interaction');
const { SMELTING, FUEL } = require('./data');

const sameStack = (a, b) => Boolean(a && b && a.type === b.type && a.metadata === b.metadata &&
  JSON.stringify(a.nbt ?? null) === JSON.stringify(b.nbt ?? null) &&
  JSON.stringify(a.components ?? []) === JSON.stringify(b.components ?? []));
const amount = (item, name) => item?.name === name ? item.count : 0;
const stackSize = (bot, item) => {
  const size = item.stackSize ?? bot.registry.itemsByName[item.name]?.stackSize;
  ensure(Number.isInteger(size) && size > 0, 'NOT_READY', '物品堆叠上限不可确认。');
  return size;
};
function sourceItem(bot, name) {
  ensure(Number.isInteger(bot.registry.itemsByName[name]?.id), 'UNKNOWN_ITEM', '当前版本没有指定物品。');
  const item = items(bot).find((item) => item.name === name && item.count > 0);
  ensure(item, 'ITEM_MISSING', 'Bot 背包中没有指定物品。');
  return item;
}
function inputCompatible(bot, window, input) {
  const output = SMELTING[input.name];
  ensure(output && bot.registry.itemsByName[output], 'UNSUPPORTED_RECIPE', '当前普通熔炉适配器没有该原料配方。');
  const current = window.inputItem(), product = window.outputItem();
  ensure(!current || sameStack(current, input), 'FURNACE_INPUT_CONFLICT', '原料槽已有不同物品或不同数据的物品，不能混放，请选择其它熔炉。');
  ensure(!product || product.name === output, 'FURNACE_OUTPUT_CONFLICT', '输出槽含有其它工艺的产品，请选择其它熔炉或明确取出后再使用。');
  ensure(!product || product.count < stackSize(bot, product), 'FURNACE_OUTPUT_FULL', '输出槽已满，请先取出产品再补充原料。');
  return output;
}
function fuelItem(bot, window, args) {
  const current = window.fuelItem();
  let name = args.fuel_name;
  if (name === undefined) {
    // 非空燃料槽只能同类补充；空槽按定义顺序优先煤炭、其次木炭等已知燃料。
    name = current?.name ?? Object.keys(FUEL).find((key) => bot.registry.itemsByName[key] && items(bot).some((i) => i.name === key && i.count > 0));
    ensure(name, 'FUEL_MISSING', '背包没有煤炭或当前适配器支持的其它燃料。');
  }
  ensure(FUEL[name] && bot.registry.itemsByName[name], 'UNSUPPORTED_FUEL', '该物品不是当前适配器支持的燃料。');
  const item = sourceItem(bot, name);
  ensure(!current || sameStack(current, item), 'FURNACE_FUEL_CONFLICT', '燃料槽已有不同燃料，不能混放；请同类补充或等待燃料槽清空。');
  return item;
}

// 等待有限的槽位/属性更新。只观察同一次操作，不重发副作用。
async function observe(ctx, read) {
  let result;
  for (let i = 0; i < 6; i++) {
    ctx.check(); result = read();
    if (result.confirmed) return result;
    if (i < 5) await ctx.pause(100);
  }
  return result;
}

module.exports = function registerFurnaceTools(registry) {
  registry.register({ name: 'furnace_add_input',
    description: '向指定普通熔炉添加或补充本次数量的原料（1～64）；允许同类追加，不覆盖其它原料/产品。输出已满先取产品。只确认投料，不表示熔炼完成，不自动寻找其它熔炉。',
    parameters: object({
      position: describe(position, '目标普通熔炉的整数方块坐标；不是新熔炉位置。'),
      item_name: describe(text, '要投入的原料真实名称，例如 raw_iron、sand。'),
      count: describe(number(1, 64, true), '本次新增投入的原料数量（1-64）；允许同类追加，不覆盖其它原料。'),
    }),
    execute(ctx, a) {
      return interact(ctx, a.position, 'furnace', async (window, adapter) => {
        const { bot } = ctx, item = sourceItem(bot, a.item_name);
        const output = inputCompatible(bot, window, item);
        ensure(count(bot, item.name) >= a.count, 'INSUFFICIENT_ITEMS', '背包原料不足以添加本次数量。');
        ensure(amount(window.inputItem(), item.name) + a.count <= stackSize(bot, item), 'FURNACE_INPUT_FULL', '原料槽剩余空间不足，未自动减少添加数量。');
        const before = adapter.inspect(window), inventoryBefore = count(bot, item.name);
        await ctx.step(() => window.putInput(item.type, item.metadata, a.count));
        const result = await observe(ctx, () => {
          const after = adapter.inspect(window);
          const inventoryDelta = count(bot, item.name) - inventoryBefore;
          const slotDelta = amount(after.input, item.name) - amount(before.input, item.name);
          const processed = amount(after.output, output) - amount(before.output, output);
          return { confirmed: inventoryDelta === -a.count && processed >= 0 && slotDelta + processed === a.count,
            inventoryDelta, slotDelta, processedDuringTransfer: processed, furnace: after };
        });
        return state(result.confirmed ? 'completed' : 'uncertain', { operation: 'add_input', item: item.name,
          requested: a.count, expectedOutput: output, ...result, before,
          note: '仅确认本次投料；加工及产物入包仍需另行查询和取出。' });
      });
    }
  });
  registry.register({ name: 'furnace_add_fuel',
    description: '向指定普通熔炉添加或补充本次数量的燃料（1～64，默认1）；fuel_name 可省略，空槽优先煤炭、没有则其它已支持可燃物。非空槽只同类追加，不替换旧燃料；count 是燃料件数，不是原料数量。',
    parameters: object({
      position: describe(position, '目标普通熔炉的整数方块坐标；不是新熔炉位置。'),
      fuel_name: describe(text, '可选：燃料真实名称；省略时空槽优先煤炭，没有则选择其它已支持燃料。'),
      count: describe(number(1, 64, true), '本次添加的燃料件数（1-64，默认 1）；不是原料数量。'),
    }, ['position']),
    execute(ctx, a) {
      return interact(ctx, a.position, 'furnace', async (window, adapter) => {
        const { bot } = ctx, item = fuelItem(bot, window, a), requested = a.count ?? 1;
        ensure(count(bot, item.name) >= requested, 'INSUFFICIENT_ITEMS', '背包所选燃料不足以添加本次数量。');
        ensure(amount(window.fuelItem(), item.name) + requested <= stackSize(bot, item), 'FURNACE_FUEL_FULL', '燃料槽剩余空间不足，未自动减少添加数量。');
        // openFurnace 的燃烧属性初始为 null，等待服务器提供基线；未知时不能猜成零。
        for (let i = 0; i < 5 && !Number.isFinite(window.fuelSeconds); i++) await ctx.pause(100);
        const before = adapter.inspect(window), inventoryBefore = count(bot, item.name);
        await ctx.step(() => window.putFuel(item.type, item.metadata, requested));
        const result = await observe(ctx, () => {
          const after = adapter.inspect(window), inventoryDelta = count(bot, item.name) - inventoryBefore;
          const slotDelta = amount(after.fuel, item.name) - amount(before.fuel, item.name);
          // 燃料加入后可能立即消耗一件点火；需观察燃烧秒数增长，不能只凭背包减少认定成功。
          const burnObserved = before.fuelSeconds !== null && after.fuelSeconds !== null && after.fuelSeconds > before.fuelSeconds;
          const consumed = requested - slotDelta;
          return { confirmed: inventoryDelta === -requested && (slotDelta === requested || consumed === 1 && burnObserved),
            inventoryDelta, slotDelta, burnObserved, furnace: after };
        });
        return state(result.confirmed ? 'completed' : 'uncertain', { operation: 'add_fuel', item: item.name,
          requested, automaticSelection: a.fuel_name === undefined, smeltsPerItem: FUEL[item.name], ...result, before,
          note: '仅确认本次加燃料；不是加工完成的报告。' });
      });
    }
  });
  registry.register({ name: 'furnace_take_output',
    description: '从指定普通熔炉取出当前输出整栈（最多64）；item_name 必填校验名称，count 可选且必须等于当前整栈数量。可在加工中取出，不要求原料/燃料槽为空；需足够背包空间并核验真实增减，不支持部分取出。',
    parameters: object({
      position: describe(position, '目标普通熔炉的整数方块坐标。'),
      item_name: describe(text, '输出槽中要取出的物品真实名称，必须与当前产物一致。'),
      count: describe(number(1, 64, true), '可选：必须等于当前输出整栈数量；不支持部分取出。'),
    }, ['position', 'item_name']),
    execute(ctx, a) {
      return interact(ctx, a.position, 'furnace', async (window, adapter) => {
        const { bot } = ctx, product = window.outputItem();
        ensure(product?.name === a.item_name && product.count > 0, 'OUTPUT_NOT_READY', '输出槽没有指定名称的产品。');
        ensure(a.count === undefined || product.count === a.count, 'OUTPUT_COUNT_MISMATCH', '本工具取出当前输出整栈，指定数量须与实际整栈数量相同；不支持部分取出。');
        ensure(Number.isInteger(window.inventoryStart) && Number.isInteger(window.inventoryEnd) && Array.isArray(window.slots), 'NOT_READY', '熔炉背包槽位尚不可读。');
        const capacity = window.slots.slice(window.inventoryStart, window.inventoryEnd).reduce((sum, item) =>
          sum + (!item ? stackSize(bot, product) : sameStack(item, product) ? Math.max(0, stackSize(bot, item) - item.count) : 0), 0);
        // 加工中输出仍可能增加，为一整栈留出容量，避免官方 putAway 在容量不足时丢弃余量。
        const requiredCapacity = window.inputItem() ? stackSize(bot, product) : product.count;
        ensure(capacity >= requiredCapacity, 'INVENTORY_FULL', '背包没有足够空间接收产品；加工中需为完整输出栈预留空间。');
        const before = adapter.inspect(window), inventoryBefore = count(bot, product.name);
        // 输出槽不能写回。使用官方整栈接口，禁止 transfer 把余量放回结果槽。
        await ctx.step(() => window.takeOutput());
        const result = await observe(ctx, () => {
          const after = adapter.inspect(window), inventoryDelta = count(bot, product.name) - inventoryBefore;
          const slotDelta = amount(after.output, product.name) - amount(before.output, product.name);
          const inputName = before.input?.name;
          const processed = inputName && SMELTING[inputName] === product.name
            ? amount(before.input, inputName) - amount(after.input, inputName) : 0;
          const countConfirmed = a.count === undefined ? inventoryDelta >= before.output.count : inventoryDelta === a.count;
          return { confirmed: countConfirmed && inventoryDelta <= stackSize(bot, product) && processed >= 0 && slotDelta + inventoryDelta === processed,
            inventoryDelta, slotDelta, processedDuringTransfer: processed, furnace: after };
        });
        return state(result.confirmed ? 'completed' : 'uncertain', { operation: 'take_output', item: product.name,
          countMode: 'entire_output', requested: a.count ?? before.output.count, taken: result.inventoryDelta, ...result, before });
      });
    }
  });
};
