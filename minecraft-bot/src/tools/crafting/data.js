// 普通熔炉适配器的唯一配方/燃料定义。仅声明已支持的原版规则；
// 使用时再以当前 registry 过滤物品，不能从模组物品名称猜测燃烧时间。
const SMELTING = Object.freeze({ raw_iron: 'iron_ingot', iron_ore: 'iron_ingot', deepslate_iron_ore: 'iron_ingot',
  raw_gold: 'gold_ingot', gold_ore: 'gold_ingot', deepslate_gold_ore: 'gold_ingot', raw_copper: 'copper_ingot',
  copper_ore: 'copper_ingot', sand: 'glass', cobblestone: 'stone', beef: 'cooked_beef', porkchop: 'cooked_porkchop',
  chicken: 'cooked_chicken', potato: 'baked_potato', oak_log: 'charcoal', birch_log: 'charcoal', spruce_log: 'charcoal' });
const woods = ['oak', 'spruce', 'birch', 'jungle', 'acacia', 'dark_oak', 'mangrove', 'cherry', 'pale_oak'];
const FUEL = Object.freeze({ coal: 8, charcoal: 8, coal_block: 80, dried_kelp_block: 20, blaze_rod: 12,
  stick: 0.5, bamboo: 0.25,
  ...Object.fromEntries(woods.flatMap((wood) => ['planks', 'log', 'wood'].map((part) => [`${wood}_${part}`, 1.5])
    .concat(['log', 'wood'].map((part) => [`stripped_${wood}_${part}`, 1.5])))) });

module.exports = { SMELTING, FUEL };
