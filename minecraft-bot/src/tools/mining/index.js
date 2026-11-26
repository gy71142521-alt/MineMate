const { object, text, number, position, describe } = require('../schema');
const { ensure, blockAt, vector, distance, plainPosition, count, items, usable, state } = require('../common');

const AIR = new Set(['air', 'cave_air', 'void_air']);
const PROTECTED = /^(?:air|cave_air|void_air|water|lava|bedrock|chest|barrel|furnace|shulker|spawner|crafting_table|command_block|portal)$/;
// 清障只允许常见天然环境方块；矿石、功能方块和未知模组方块不会被自动破坏。
const CLEARABLE = /^(stone|cobblestone|dirt|coarse_dirt|rooted_dirt|grass_block|sand|red_sand|gravel|clay|snow|netherrack|end_stone|deepslate|tuff|calcite|dripstone_block|basalt|blackstone|\w+_(?:log|wood|leaves|stem|hyphae))$/;

function isClearable(block) {
  return Boolean(block && !AIR.has(block.name) && !PROTECTED.test(block.name) && CLEARABLE.test(block.name));
}

function chooseTool(bot, block, requested) {
  if (requested) {
    const selected = items(bot).find((item) => item.name === requested);
    ensure(selected, 'TOOL_MISSING', '背包中没有指定的挖掘工具。');
    usable(selected);
    ensure(block.canHarvest(selected.type), 'WRONG_TOOL', '指定工具无法采集该方块。');
    return selected;
  }
  const requiresTool = !block.canHarvest(undefined);
  const handDigTime = typeof block.digTime === 'function' ? block.digTime(undefined) : Infinity;
  return items(bot).find((item) => (block.harvestTools?.[item.type] ||
    (requiresTool && !block.harvestTools && block.canHarvest(item.type)) ||
    (!requiresTool && !block.harvestTools && typeof block.digTime === 'function' && block.digTime(item.type) < handDigTime)) &&
    block.canHarvest(item.type) && (!item.maxDurability || item.maxDurability - item.durabilityUsed > 1));
}

async function digBlock(ctx, a, { pathClear = false, visible = null, digFace = null } = {}) {
  const { bot } = ctx;
  let block = blockAt(ctx, a.position, a.max_distance ?? 4.5);
  ensure(block.name === a.block_name, 'BLOCK_CHANGED', '目标方块与指定名称不一致。');
  ensure(!PROTECTED.test(block.name), 'PROTECTED_BLOCK', '该方块不允许自动破坏。');
  if (pathClear) ensure(isClearable(block), 'PATH_BLOCK_NOT_SAFE', '路径上的方块不在自动清障允许范围内。');
  const floor = bot.entity.position.floored();
  ensure(!(floor.x === a.position.x && floor.z === a.position.z && floor.y - 1 === a.position.y), 'UNSAFE_TARGET', '不能挖掘 Bot 脚下的支撑方块。');
  const nearFloor = Math.max(Math.abs(a.position.x - floor.x), Math.abs(a.position.z - floor.z)) <= 1;
  ensure(!(nearFloor && a.position.y < floor.y), 'UNSAFE_TARGET', '目标在 Bot 脚边下方，挖掉可能把自己困在坑里。');
  ensure(block.hardness >= 0 && typeof block.canHarvest === 'function', 'NOT_DIGGABLE', '方块无法安全挖掘。');
  const tool = chooseTool(bot, block, a.tool_name);
  if (tool) {
    usable(tool);
    if (bot.heldItem?.type !== tool.type) await ctx.step(() => bot.equip(tool, 'hand'));
  } else {
    ensure(block.canHarvest(undefined), 'TOOL_MISSING', '背包中没有合适工具，且空手无法采集该方块。');
    if (bot.heldItem && typeof bot.unequip === 'function') await ctx.step(() => bot.unequip('hand'));
  }
  if (bot.heldItem) usable(bot.heldItem);
  block = blockAt(ctx, a.position, a.max_distance ?? 4.5);
  ensure(block.name === a.block_name && block.canHarvest(bot.heldItem?.type) && bot.canDigBlock(block), 'NOT_DIGGABLE', '目标已变化或当前无法挖掘。');
  ensure(visible ? visible(block) : bot.canSeeBlock(block), 'NO_LINE_OF_SIGHT', '看不到目标方块，不能隔墙挖掘。');
  const before = count(bot, a.block_name);
  ctx.cleanup(() => bot.stopDigging());
  await ctx.step(() => digFace ? bot.dig(block, true, digFace) : bot.dig(block));
  const after = bot.blockAt(vector(a.position));
  return state(after && AIR.has(after.name) ? 'completed' : 'uncertain', {
    position: a.position, blockRemoved: after ? AIR.has(after.name) : null,
    sameNameInventoryDelta: count(bot, a.block_name) - before, pickupVerified: false
  });
}

function lineBlocks(bot, target) {
  const origin = bot.entity.position.offset(0, 1.5, 0);
  const feet = bot.entity.position.floored();
  const destination = vector(target).offset(0.5, 0.5, 0.5);
  const length = distance(origin, destination);
  const blocks = [];
  const seen = new Set();
  for (let travelled = 0; travelled <= length; travelled += 0.25) {
    const ratio = length === 0 ? 1 : travelled / length;
    const point = origin.plus(destination.minus(origin).scaled(ratio));
    const position = point.floored();
    const key = position.toString();
    if (seen.has(key) || (position.x === target.x && position.y === target.y && position.z === target.z) ||
        (position.x === feet.x && position.z === feet.z && position.y >= feet.y && position.y <= feet.y + 1)) continue;
    seen.add(key);
    const block = bot.blockAt(position);
    if (block && !AIR.has(block.name)) blocks.push(block);
  }
  return blocks;
}

function registerMining(registry) {
  require('./vein')(registry, { digBlock });
  require('./sources')(registry);
  registry.register({ name: 'inspect_block', description: '只读查询明确坐标的实际方块名称、距离和可挖/可见情况；用于挖掘前识别目标，不检查放置。', mutates: false,
    parameters: object({
      position: describe(position, '要查询的方块整数坐标 {x,y,z}，必须是真实世界坐标。'),
    }),
    execute(ctx, a) {
      const block = blockAt(ctx, a.position);
      return state('completed', { name: block.name, position: plainPosition(block.position), distance: distance(ctx.bot.entity.position, block.position),
        diggable: Boolean(ctx.bot.canDigBlock(block)), visible: Boolean(ctx.bot.canSeeBlock(block)) });
    }
  });
  registry.register({ name: 'find_block', description: '在已加载区块的有限半径内查找指定方块，最多返回 16 项。', mutates: false,
    parameters: object({
      block_name: describe(text, '要查找的方块真实名称，例如 oak_log、stone；来自 resolve_resource 或已知名称。'),
      radius: describe(number(1, 16, true), '搜索半径（1-16 格，默认 5）；结果不保证可见或可达。'),
      max_results: describe(number(1, 16, true), '最多返回候选数量（1-16，默认 16）。'),
    }, ['block_name']),
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
  registry.register({ name: 'clear_path', description: '受控清理 Bot 到明确坐标之间当前视线/通行线上的少量天然方块；每次最多清除8个，不挖掘功能方块、矿石、液体、脚下支撑或未知方块，清障后必须重新寻路和核验。',
    parameters: object({ position, max_blocks: number(1, 8, true), max_distance: number(1, 8, false), tool_name: text }, ['position']),
    async execute(ctx, a) {
      const maximum = a.max_blocks ?? 2;
      const maxDistance = a.max_distance ?? 8;
      ensure(distance(ctx.bot.entity.position, vector(a.position)) <= maxDistance + 4.5, 'PATH_OUT_OF_RANGE', '清障目标超出本次有限范围。');
      let cleared = 0;
      const clearedBlocks = [];
      for (let i = 0; i < maximum; i++) {
        const blockers = lineBlocks(ctx.bot, a.position).filter((block) => isClearable(block));
        const blocker = blockers[0];
        if (!blocker) break;
        ensure(distance(ctx.bot.entity.position, blocker.position) <= 4.5, 'PATH_BLOCK_OUT_OF_REACH', '最近路径障碍暂时超出挖掘距离，请先移动或换站位。');
        const result = await digBlock(ctx, { block_name: blocker.name, position: plainPosition(blocker.position), max_distance: 4.5, tool_name: a.tool_name }, { pathClear: true });
        ensure(result.status === 'completed' && result.blockRemoved === true, 'PATH_CLEAR_UNCONFIRMED', '路径方块移除未确认。');
        cleared += 1;
        clearedBlocks.push({ name: blocker.name, position: plainPosition(blocker.position) });
      }
      return state('completed', { target: plainPosition(vector(a.position)), cleared, clearedBlocks,
        remainingBlocked: lineBlocks(ctx.bot, a.position).some((block) => isClearable(block)) });
    }
  });
  registry.register({ name: 'mine_block', description: '挖掘明确坐标的一个方块；名称未知先 inspect_block。tool_name 可指定钻石斧等背包工具；校验距离/视线/耐久/采集条件，不自动拾取。',
    parameters: object({
      block_name: describe(text, '目标方块的当前真实名称；必须与 position 处方块一致。'),
      position: describe(position, '要挖的单个方块整数坐标 {x,y,z}；不是放置位置。'),
      max_distance: describe(number(1, 4.5), '允许的最大交互距离（1-4.5 格，默认 4.5）；超出请先导航。'),
      tool_name: describe(text, '可选：背包中要使用的工具真实名称，例如 diamond_axe；省略时自动选择可用工具。'),
    }, ['block_name', 'position']),
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
