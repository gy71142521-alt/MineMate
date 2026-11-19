const { object, text, number } = require('../schema');
const { ensure, items, state } = require('../common');
const { SMELTING, FUEL } = require('./index');

module.exports = function registerRecipes(registry) {
  registry.register({ name: 'inspect_smelting_recipe', description: '只读查询现有普通熔炉适配器支持的原料、产物、燃料用量与背包缺口，不投入物品。仅表示已实现的基础熔炼能力。', mutates: false,
    parameters: object({ item_name: text, count: number(1, 8, true) }, ['item_name']),
    execute({ bot }, a) {
      const output = SMELTING[a.item_name], amount = a.count ?? 1, inventory = items(bot);
      ensure(output && bot.registry.itemsByName[a.item_name] && bot.registry.itemsByName[output], 'UNSUPPORTED_RECIPE', '当前版本或适配器不支持该熔炼配方。');
      const count = (name) => inventory.filter((item) => item.name === name).reduce((sum, item) => sum + item.count, 0);
      const available = count(a.item_name);
      return state('completed', { input: { name: a.item_name, count: amount, available, missing: Math.max(0, amount - available) },
        output: { name: output, count: amount },
        fuels: Object.entries(FUEL).filter(([name]) => bot.registry.itemsByName[name]).map(([name, capacity]) => {
          const required = Math.ceil(amount / capacity), available = count(name);
          return { name, count: required, available, missing: Math.max(0, required - available) };
        }) });
    }
  });
  registry.register({ name: 'inspect_recipe', description: '只读查询当前版本合成配方、材料、整批产量、工作台需求和背包缺口。不合成，不寻找或打开工作台。', mutates: false,
    parameters: object({ item_name: text, count: number(1, 64, true), max_results: number(1, 16, true) }, ['item_name']),
    execute({ bot }, a) {
      const type = bot.registry.itemsByName[a.item_name]?.id;
      ensure(Number.isInteger(type), 'UNKNOWN_ITEM', '当前版本没有指定物品。');
      // 官方 recipesAll 的第三参只用于过滤 requiresTable；true 仅展示含工作台配方，不代表台子存在。
      const all = bot.recipesAll(type, null, true), maximum = a.max_results ?? 8;
      const inventory = items(bot);
      const copy = (entry, amount) => ({ name: bot.registry.items[entry.id]?.name ?? null, type: entry.id,
        metadata: entry.metadata ?? null, count: amount });
      const recipes = all.map((recipe) => {
        const operations = Math.ceil((a.count ?? 1) / recipe.result.count);
        const ingredients = new Map();
        for (const entry of recipe.inShape ? recipe.inShape.flat() : recipe.ingredients ?? []) {
          if (!entry || entry.id < 0) continue;
          const key = `${entry.id}:${entry.metadata ?? '*'}`;
          const value = ingredients.get(key) ?? copy(entry, 0);
          value.count += recipe.inShape ? 1 : Math.abs(entry.count);
          ingredients.set(key, value);
        }
        return { requiresTable: Boolean(recipe.requiresTable), operations,
          output: copy(recipe.result, recipe.result.count * operations),
          ingredients: [...ingredients.values()].map((entry) => {
            const needed = entry.count * operations;
            const available = inventory.filter((item) => item.type === entry.type && (entry.metadata === null || entry.metadata === item.metadata))
              .reduce((sum, item) => sum + item.count, 0);
            return { ...entry, count: needed, available, missing: Math.max(0, needed - available) };
          }),
          delta: recipe.delta.map((entry) => copy(entry, entry.count * operations)) };
      });
      // 优先展示材料缺口较小的配方，避免版本枚举顺序把已有材料的配方截掉。
      recipes.sort((a, b) => a.ingredients.reduce((s, i) => s + i.missing, 0) - b.ingredients.reduce((s, i) => s + i.missing, 0));
      return state('completed', { item: a.item_name, recipes: recipes.slice(0, maximum), truncated: all.length > maximum });
    }
  });
};
