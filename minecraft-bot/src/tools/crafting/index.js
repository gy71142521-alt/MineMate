const { object, text, number, position } = require('../schema');
const { ensure, count, itemByName, state } = require('../common');
const { interact } = require('../interaction');

// 基础熔炉白名单，其他版本/配方需单独适配，不猜测未知加工规则。
const SMELTING = Object.freeze({ raw_iron: 'iron_ingot', iron_ore: 'iron_ingot', deepslate_iron_ore: 'iron_ingot',
  raw_gold: 'gold_ingot', gold_ore: 'gold_ingot', deepslate_gold_ore: 'gold_ingot', raw_copper: 'copper_ingot',
  copper_ore: 'copper_ingot', sand: 'glass', cobblestone: 'stone', beef: 'cooked_beef', porkchop: 'cooked_porkchop',
  chicken: 'cooked_chicken', potato: 'baked_potato', oak_log: 'charcoal', birch_log: 'charcoal', spruce_log: 'charcoal' });
const FUEL = Object.freeze({ coal: 8, charcoal: 8, oak_planks: 1.5, birch_planks: 1.5, spruce_planks: 1.5, stick: 0.5 });

function registerCrafting(registry) {
  registry.register({ name: 'craft_item', description: '检查当前版本配方与材料，在指定工作台合成目标物品；count 为期望产物数量，按整批配方取整。',
    parameters: object({ item_name: text, count: number(1, 16, true), position }, ['item_name', 'count']),
    async execute(ctx, a) {
      const { bot } = ctx;
      const type = bot.registry.itemsByName[a.item_name]?.id;
      ensure(Number.isInteger(type), 'UNKNOWN_ITEM', '当前版本没有指定物品。');
      const table = a.position ? require('../common').blockAt(ctx, a.position) : null;
      ensure(!table || table.name === 'crafting_table', 'WRONG_BLOCK', '指定位置不是工作台。');
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
    parameters: object({ position, action: { type: 'string', enum: ['start', 'status', 'take'] }, item_name: text, fuel_name: text, count: number(1, 8, true), fuel_count: number(1, 16, true) }, ['position', 'action']),
    async execute(ctx, a) {
      const { bot } = ctx;
      let input, fuel;
      const amount = a.count ?? 1;
      if (a.action === 'start') {
        ensure(SMELTING[a.item_name] && FUEL[a.fuel_name], 'UNSUPPORTED_RECIPE', '原料或燃料不在基础熔炼白名单。');
        ensure(bot.registry.itemsByName[SMELTING[a.item_name]], 'UNSUPPORTED_RECIPE', '当前版本没有该熔炼产物。');
        input = itemByName(bot, a.item_name); fuel = itemByName(bot, a.fuel_name);
        const fuelCount = a.fuel_count ?? Math.ceil(amount / FUEL[a.fuel_name]);
        ensure(count(bot, a.item_name) >= amount && count(bot, a.fuel_name) >= fuelCount && fuelCount * FUEL[a.fuel_name] >= amount, 'MATERIALS_MISSING', '熔炉原料或燃料不足。');
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
