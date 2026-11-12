const { object, text, number, position } = require('../schema');
const { ensure, blockAt, vector, distance, plainPosition, count, items, usable, state } = require('../common');

function registerMining(registry) {
  registry.register({ name: 'find_block', description: '在已加载区块的有限半径内查找指定方块，最多返回 16 项。', mutates: false,
    parameters: object({ block_name: text, radius: number(1, 16, true), max_results: number(1, 16, true) }, ['block_name']),
    execute(ctx, a) {
      const definition = ctx.bot.registry.blocksByName[a.block_name];
      ensure(definition, 'UNKNOWN_BLOCK', '当前版本不存在指定方块。');
      const radius = a.radius ?? 5, maximum = a.max_results ?? 8;
      const found = ctx.bot.findBlocks({ matching: definition.id, maxDistance: radius, count: maximum + 1 });
      const results = found.map((p) => ({ name: a.block_name, position: plainPosition(p), distance: distance(ctx.bot.entity.position, p) }))
        .filter((entry) => entry.distance <= radius).sort((x, y) => x.distance - y.distance);
      return state('completed', { blocks: results.slice(0, maximum), truncated: results.length > maximum });
    }
  });
  registry.register({ name: 'mine_block', description: '只挖掘明确指定坐标的单个方块，检查距离、工具与耐久；不自动拾取。',
    parameters: object({ block_name: text, position, max_distance: number(1, 4.5) }, ['block_name', 'position']),
    async execute(ctx, a) {
      const { bot } = ctx;
      let block = blockAt(ctx, a.position, a.max_distance ?? 4.5);
      ensure(block.name === a.block_name, 'BLOCK_CHANGED', '目标方块与指定名称不一致。');
      ensure(!/air$|water|lava|bedrock|chest|barrel|furnace|shulker|spawner|crafting_table/.test(block.name), 'PROTECTED_BLOCK', '该方块不允许通过基础挖掘工具破坏。');
      const floor = bot.entity.position.floored();
      ensure(!(floor.x === a.position.x && floor.z === a.position.z && floor.y - 1 === a.position.y), 'UNSAFE_TARGET', '不能挖掘 Bot 脚下的支撑方块。');
      ensure(block.hardness >= 0 && typeof block.canHarvest === 'function', 'NOT_DIGGABLE', '方块无法安全挖掘。');
      if (!block.canHarvest(bot.heldItem?.type)) {
        const tool = items(bot).find((item) => block.canHarvest(item.type) && (!item.maxDurability || item.maxDurability - item.durabilityUsed > 1));
        ensure(tool, 'TOOL_MISSING', '缺少可采集此方块且耐久足够的工具。');
        usable(tool);
        await ctx.step(() => bot.equip(tool, 'hand'));
      }
      if (bot.heldItem) usable(bot.heldItem);
      block = blockAt(ctx, a.position, a.max_distance ?? 4.5);
      ensure(block.name === a.block_name && block.canHarvest(bot.heldItem?.type) && bot.canDigBlock(block), 'NOT_DIGGABLE', '目标已变化或当前无法挖掘。');
      ensure(bot.canSeeBlock(block), 'NO_LINE_OF_SIGHT', '看不到目标方块，不能隔墙挖掘。');
      const before = count(bot, a.block_name);
      ctx.cleanup(() => bot.stopDigging());
      await ctx.step(() => bot.dig(block));
      const after = bot.blockAt(vector(a.position));
      return state(after && ['air', 'cave_air', 'void_air'].includes(after.name) ? 'completed' : 'uncertain', {
        position: a.position, blockRemoved: after ? ['air', 'cave_air', 'void_air'].includes(after.name) : null,
        sameNameInventoryDelta: count(bot, a.block_name) - before, pickupVerified: false
      });
    }
  });
}
module.exports = registerMining;
