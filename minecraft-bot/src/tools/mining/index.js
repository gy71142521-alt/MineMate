const { object, text, number, position } = require('../schema');
const { ensure, blockAt, vector, distance, plainPosition, count, items, usable, state } = require('../common');

function registerMining(registry) {
  require('./sources')(registry);
  registry.register({ name: 'inspect_block', description: '只读查询明确坐标的实际方块名称、距离和可挖/可见情况；用于挖掘前识别目标，不检查放置。', mutates: false,
    parameters: object({ position }),
    execute(ctx, a) {
      const block = blockAt(ctx, a.position);
      return state('completed', { name: block.name, position: plainPosition(block.position), distance: distance(ctx.bot.entity.position, block.position),
        diggable: Boolean(ctx.bot.canDigBlock(block)), visible: Boolean(ctx.bot.canSeeBlock(block)) });
    }
  });
  registry.register({ name: 'find_block', description: '在已加载区块的有限半径内查找指定方块，最多返回 16 项。', mutates: false,
    parameters: object({ block_name: text, radius: number(1, 16, true), max_results: number(1, 16, true) }, ['block_name']),
    execute(ctx, a) {
      const definition = ctx.bot.registry.blocksByName[a.block_name];
      ensure(definition, 'UNKNOWN_BLOCK', '当前版本不存在指定方块。');
      const radius = a.radius ?? 5, maximum = a.max_results ?? 8;
      const found = ctx.bot.findBlocks({ matching: definition.id, maxDistance: radius, count: maximum + 1 });
      const results = found.map((p) => {
        const block = ctx.bot.blockAt(p);
        return {
          name: a.block_name,
          position: plainPosition(p),
          distance: distance(ctx.bot.entity.position, p),
          visible: Boolean(block && ctx.bot.canSeeBlock(block)),
          diggable: Boolean(block && ctx.bot.canDigBlock(block)),
        };
      }).filter((entry) => entry.distance <= radius).sort((x, y) => x.distance - y.distance);
      return state('completed', { blocks: results.slice(0, maximum), truncated: results.length > maximum });
    }
  });
  registry.register({ name: 'mine_block', description: '挖掘明确坐标的一个方块；名称未知先 inspect_block。tool_name 可指定钻石斧等背包工具；校验距离/视线/耐久/采集条件，不自动拾取。',
    parameters: object({ block_name: text, position, max_distance: number(1, 4.5), tool_name: text }, ['block_name', 'position']),
    async execute(ctx, a) {
      const { bot } = ctx;
      let block = blockAt(ctx, a.position, a.max_distance ?? 4.5);
      ensure(block.name === a.block_name, 'BLOCK_CHANGED', '目标方块与指定名称不一致。');
      ensure(!/air$|water|lava|bedrock|chest|barrel|furnace|shulker|spawner|crafting_table/.test(block.name), 'PROTECTED_BLOCK', '该方块不允许通过基础挖掘工具破坏。');
      const floor = bot.entity.position.floored();
      ensure(!(floor.x === a.position.x && floor.z === a.position.z && floor.y - 1 === a.position.y), 'UNSAFE_TARGET', '不能挖掘 Bot 脚下的支撑方块。');
      const nearFloor = Math.max(Math.abs(a.position.x - floor.x), Math.abs(a.position.z - floor.z)) <= 1;
      ensure(!(nearFloor && a.position.y < floor.y), 'UNSAFE_TARGET', '目标在 Bot 脚边下方，挖掉可能把自己困在坑里；先退后或换站位。');
      ensure(block.hardness >= 0 && typeof block.canHarvest === 'function', 'NOT_DIGGABLE', '方块无法安全挖掘。');
      if (a.tool_name) {
        const selected = items(bot).find((item) => item.name === a.tool_name);
        ensure(selected, 'TOOL_MISSING', '背包中没有指定的挖掘工具。');
        usable(selected);
        ensure(block.canHarvest(selected.type), 'WRONG_TOOL', '指定工具无法采集该方块，请选择合适的工具。');
        await ctx.step(() => bot.equip(selected, 'hand'));
      } else {
        // 自动模式优先选择背包中可用的合适工具；只有没有工具时才尝试空手。
        // harvestTools 是 Mineflayer/Prismarine 对“必须使用工具”的运行时定义。
        // 对可空手采集的方块，不能把背包中的普通物品误当成工具。
        const requiresTool = !block.canHarvest(undefined);
        const handDigTime = typeof block.digTime === 'function' ? block.digTime(undefined) : Infinity;
        const tool = items(bot).find((item) => (block.harvestTools?.[item.type] ||
          (requiresTool && !block.harvestTools && block.canHarvest(item.type)) ||
          (!requiresTool && !block.harvestTools && typeof block.digTime === 'function' &&
            block.digTime(item.type) < handDigTime)) &&
          block.canHarvest(item.type) && (!item.maxDurability || item.maxDurability - item.durabilityUsed > 1));
        if (tool) {
          usable(tool);
          if (bot.heldItem?.type !== tool.type) await ctx.step(() => bot.equip(tool, 'hand'));
        } else {
          ensure(block.canHarvest(undefined), 'TOOL_MISSING', '背包中没有合适工具，且空手无法采集该方块。');
          if (bot.heldItem && typeof bot.unequip === 'function') await ctx.step(() => bot.unequip('hand'));
        }
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
