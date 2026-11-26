const { object, text, number, position, describe } = require('../schema');
const { ensure, count, itemByName, state } = require('../common');
const { interact } = require('../interaction');

const { SMELTING, FUEL } = require('./data');

function registerCrafting(registry) {
  require('./furnace')(registry);
  registry.register({ name: 'craft_item', description: '检查当前版本配方与材料并合成目标物品；position 仅表示已有工作台，不需要工作台的配方省略它；本工具不放置产物。',
    parameters: object({
      item_name: describe(text, '要合成的目标物品真实名称，例如 oak_planks、wooden_axe。'),
      count: describe(number(1, 16, true), '期望新增的产物数量（1-16）；多产物配方可能按整批产出更多。'),
      position: describe(position, '可选：已有工作台的整数坐标；不需要工作台的配方必须省略，本工具不放置工作台。'),
    }, ['item_name', 'count']),
    async execute(ctx, a) {
      const { bot } = ctx;
      const type = bot.registry.itemsByName[a.item_name]?.id;
      ensure(Number.isInteger(type), 'UNKNOWN_ITEM', '当前版本没有指定物品。');
      const table = a.position ? require('../common').blockAt(ctx, a.position) : null;
      ensure(!table || table.name === 'crafting_table', 'WRONG_TABLE', 'position 必须是已有工作台；不需要工作台的配方请省略 position。');
      ensure(bot.recipesAll(type, null, table).length > 0, 'NO_RECIPE', '没有当前条件下可用的合成配方。');
      const recipe = bot.recipesFor(type, null, a.count, table)[0];
      ensure(recipe, 'MATERIALS_MISSING', '材料不足，无法完成指定数量的合成。');
      const operations = Math.ceil(a.count / recipe.result.count), expected = operations * recipe.result.count;
      ensure(expected <= 64, 'OUT_OF_RANGE', '单次合成产物超过 64 个。');
      const before = count(bot, a.item_name);
      ensure(!bot.currentWindow, 'WINDOW_BUSY', '已有窗口打开。');
      // craft 自行管理工作台窗口；异常/取消也清理迟到的窗口。
      const onWindow = (window) => { if (ctx.signal.aborted) bot.closeWindow(window); };
      bot.on('windowOpen', onWindow);
      ctx.cleanup(() => { if (bot.currentWindow) return bot.closeWindow(bot.currentWindow); });
      try { for (let i = 0; i < operations; i++) await ctx.step(() => bot.craft(recipe, 1, table)); }
      finally { bot.removeListener('windowOpen', onWindow); if (bot.currentWindow) await bot.closeWindow(bot.currentWindow); }
      const delta = count(bot, a.item_name) - before;
      return state(delta >= expected ? 'completed' : 'uncertain', { item: a.item_name, requested: a.count, expected, actualIncrease: delta });
    }
  });
  registry.register({ name: 'smelt_item', description: '分阶段操作普通熔炉：start 放入原料燃料并返回 pending；status 查进度；take 取出已确认的整批产物。',
    parameters: object({
      position: describe(position, '目标普通熔炉的整数方块坐标。'),
      action: describe({ type: 'string', enum: ['start', 'status', 'take'] }, 'start 开始投料、status 查询进度、take 取出已确认整批产物。'),
      item_name: describe(text, 'start/take 使用的原料或产物真实名称。'),
      fuel_name: describe(text, '可选：start 使用的燃料真实名称。'),
      count: describe(number(1, 64, true), 'start/take 的整批数量（1-64）。'),
      fuel_count: describe(number(1, 64, true), '可选：start 要投入的燃料件数（1-64）。'),
    }, ['position', 'action']),
    async execute(ctx, a) {
      const { bot } = ctx;
      let input, fuel;
      const amount = a.count ?? 1;
      if (a.action === 'start') {
        ensure(SMELTING[a.item_name] && FUEL[a.fuel_name], 'UNSUPPORTED_RECIPE', '原料或燃料不在基础熔炼白名单。');
        ensure(bot.registry.itemsByName[SMELTING[a.item_name]], 'UNSUPPORTED_RECIPE', '当前版本没有该熔炼产物。');
        input = itemByName(bot, a.item_name); fuel = itemByName(bot, a.fuel_name);
        const fuelCount = a.fuel_count ?? Math.ceil(amount / FUEL[a.fuel_name]);
        ensure(fuelCount <= 64, 'FURNACE_FUEL_FULL', '所需燃料超过单槽64件，请使用熔炉子工具分次添加或熔炼 Skill。');
        ensure(count(bot, a.item_name) >= amount && count(bot, a.fuel_name) >= fuelCount && fuelCount * FUEL[a.fuel_name] >= amount, 'MATERIALS_MISSING', '熔炉原料或燃料不足。');
        ensure(a.item_name !== a.fuel_name || count(bot, a.item_name) >= amount + fuelCount, 'MATERIALS_MISSING', '同种物品作为原料和燃料时，背包总量不足。');
      }
      return interact(ctx, a.position, 'furnace', async (window, adapter) => {
        const before = adapter.inspect(window);
        if (a.action === 'status') {
          const ready = before.output && before.output.count >= amount && (!a.item_name || before.output.name === a.item_name);
          return state(ready ? 'completed' : 'pending', { ...before, outputReady: Boolean(ready) });
        }
        if (a.action === 'take') {
          const output = window.outputItem();
          ensure(a.item_name && output?.name === a.item_name && output.count === amount, 'OUTPUT_NOT_READY', '产物不存在或数量不匹配；不提前取出未完成的批次。');
          const initial = count(bot, a.item_name);
          await ctx.step(() => window.takeOutput());
          const delta = count(bot, a.item_name) - initial;
          return state(delta === amount ? 'completed' : 'uncertain', { item: a.item_name, taken: delta, furnace: adapter.inspect(window) });
        }
        ensure(!before.input && !before.fuel && !before.output, 'FURNACE_BUSY', '熔炉含有其他物品，不覆盖或重复投入。');
        await ctx.step(() => window.putInput(input.type, null, amount));
        await ctx.step(() => window.putFuel(fuel.type, null, a.fuel_count ?? Math.ceil(amount / FUEL[a.fuel_name])));
        const actual = adapter.inspect(window);
        return state(actual.input?.name === a.item_name || actual.output?.name === SMELTING[a.item_name] ? 'pending' : 'uncertain', {
          ...actual, expectedOutput: SMELTING[a.item_name], expectedCount: amount,
          note: '原料与燃料操作已提交，请稍后 status 查询，确认产物后 take。'
        });
      });
    }
  });

}
module.exports = registerCrafting;
// 只读配方查询复用同一份加工能力定义，不在 Python 复制白名单。
module.exports.SMELTING = SMELTING;
module.exports.FUEL = FUEL;
