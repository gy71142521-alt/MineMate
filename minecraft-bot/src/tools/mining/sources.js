const { object, text, number, describe } = require('../schema');
const { ensure, state } = require('../common');

// 当前 minecraft-data / prismarine-block drops 类型：数字或 {drop: 数字/{id, metadata}}。
// 这里只报告静态注册表的可能来源，不能保证附魔、服务端数据包或模组的实际掉落。
function dropId(value) {
  if (Number.isInteger(value)) return value;
  const drop = value?.drop;
  return Number.isInteger(drop) ? drop : Number.isInteger(drop?.id) ? drop.id : null;
}

module.exports = function registerMiningSources(registry) {
  registry.register({ name: 'inspect_mining_sources',
    description: '只读从当前运行时方块 drops 数据查询物品的可能采集来源。不会扫描或挖掘世界；不保证附魔/数据包实际掉落，必须以最终背包验证。可指定 block_name 检查来源。',
    parameters: object({
      item_name: describe(text, '要查询掉落来源的物品真实名称，例如 cobblestone、raw_iron。'),
      block_name: describe(text, '可选：只检查某个来源方块名称，例如 stone；省略则查询所有支持来源。'),
      max_results: describe(number(1, 16, true), '最多返回多少候选来源（1-16，默认 16）。'),
    }, ['item_name']), mutates: false,
    execute({ bot }, a) {
      const item = bot.registry?.itemsByName?.[a.item_name];
      ensure(Number.isInteger(item?.id), 'UNKNOWN_ITEM', '当前版本不存在指定物品。');
      const table = bot.registry?.blocksByName;
      ensure(table && typeof table === 'object', 'NOT_READY', '方块注册表尚不可用。');
      if (a.block_name) ensure(table[a.block_name], 'UNKNOWN_BLOCK', '当前版本不存在指定来源方块。');
      const entries = a.block_name ? [[a.block_name, table[a.block_name]]] : Object.entries(table);
      let known = false;
      const sources = [];
      for (const [name, block] of entries) {
        if (!Array.isArray(block?.drops)) continue;
        known = true;
        if (block.drops.some((drop) => dropId(drop) === item.id)) sources.push({ name });
      }
      sources.sort((a, b) => a.name.localeCompare(b.name));
      const maximum = a.max_results ?? 16;
      return state('completed', { item: a.item_name, sources: sources.slice(0, maximum), available: known,
        truncated: sources.length > maximum, source: 'runtime_registry', lootRulesVerified: false });
    }
  });
};
