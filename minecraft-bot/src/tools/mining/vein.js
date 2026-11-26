// 矿脉局部通道规划：只读取已加载方块，逐步建议动作；不更改 pathfinder 的 canDig。
const { object, text, number, position } = require('../schema');
const { ensure, vector, plainPosition, distance, items, state } = require('../common');

const AIR = new Set(['air', 'cave_air', 'void_air']);
const TERRAIN = new Set(['stone', 'deepslate', 'tuff', 'calcite', 'granite', 'diorite', 'andesite',
  'dirt', 'coarse_dirt', 'rooted_dirt', 'grass_block', 'netherrack', 'end_stone', 'basalt', 'blackstone']);
const DANGER = /water|lava|fire|magma|campfire|cactus|powder_snow|portal|berry_bush/;
const FALLING = /^(?:sand|red_sand|gravel|.*concrete_powder|.*anvil|pointed_dripstone)$/;
const DIRECTIONS = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]];
const key = (p) => `${p.x},${p.y},${p.z}`;
const air = (b) => b && AIR.has(b.name);
const full = (b) => b?.boundingBox === 'block' && b.shapes?.some(s =>
  s.length === 6 && s.every((n, i) => n === (i < 3 ? 0 : 1)));
const isOre = (name) => /(?:^|[_:])ore$/.test(name) || name === 'ancient_debris';

function canHarvest(bot, block, requested) {
  if (!block || block.hardness < 0 || typeof block.canHarvest !== 'function') return false;
  const available = items(bot).filter(i => !i.maxDurability ||
    Number.isFinite(i.durabilityUsed) && i.maxDurability - i.durabilityUsed > 1);
  if (requested) return available.some(i => i.name === requested && block.canHarvest(i.type));
  return block.canHarvest(undefined) || available.some(i => block.canHarvest(i.type));
}

function safeTerrain(bot, read, p, start, removed) {
  const b = read(p);
  if (!b || !TERRAIN.has(b.name) || !canHarvest(bot, b)) return false;
  return safeRemoval(read, p, start, removed);
}

function safeRemoval(read, p, start, removed = new Set()) {
  if (p.x === start.x && p.z === start.z && p.y < start.y) return false;
  for (const d of DIRECTIONS) {
    const q = p.offset(...d), neighbor = read(q);
    if (!neighbor || DANGER.test(neighbor.name)) return false;
    if (d[1] === 1 && FALLING.test(neighbor.name) && !removed.has(key(q))) return false;
  }
  return true;
}

// 对多个表面采样，实际 raycast 使用碰撞形状；不修改 bot.entity 来模拟站位。
function sees(bot, read, standing, target, removed = new Set(), maxReach = 4.5) {
  const eye = standing.offset(0, bot.entity.eyeHeight ?? 1.62, 0);
  for (const offset of [[0.5, 0.5, 0.5], [0.01, 0.5, 0.5], [0.99, 0.5, 0.5],
    [0.5, 0.01, 0.5], [0.5, 0.99, 0.5], [0.5, 0.5, 0.01], [0.5, 0.5, 0.99]]) {
    const end = target.offset(...offset), gap = distance(eye, end);
    if (gap > maxReach || gap < 0.001) continue;
    const direction = end.minus(eye).normalize();
    let loaded = true;
    for (let d = 0; d <= gap; d += 0.25) if (!read(eye.plus(direction.scaled(d)).floored())) { loaded = false; break; }
    if (!loaded) continue;
    const hit = bot.world.raycast(eye, direction, gap + 0.02, (block, iterator) =>
      !removed.has(key(block.position)) && (block.position.equals(target) ||
        Boolean(iterator.intersect(block.shapes, block.position))));
    if (hit?.position?.equals(target)) return true;
  }
  return false;
}

function planRoute(ctx, a) {
  ctx.check();
  const { bot } = ctx, target = vector(a.position), start = bot.entity.position.floored();
  const radius = a.radius ?? 12, maximum = a.max_clear_blocks ?? 16, purpose = a.purpose ?? 'mine';
  ensure(distance(bot.entity.position, target) <= radius + 4.5, 'OUT_OF_RANGE', '目标超出矿脉局部通道规划范围。');
  ensure(typeof bot.world?.raycast === 'function', 'NOT_READY', '世界射线接口尚未就绪。');
  const cache = new Map();
  const read = (p) => {
    const q = p.floored(), k = key(q);
    if (!cache.has(k)) cache.set(k, bot.blockAt(q));
    return cache.get(k);
  };
  ensure(read(target), 'CHUNK_UNAVAILABLE', '目标区块尚未加载。');
  if (purpose === 'mine') {
    ensure(read(target).name === a.block_name && isOre(a.block_name), 'BLOCK_CHANGED', '矿石已改变或名称不是矿石类型。');
    ensure(canHarvest(bot, read(target), a.tool_name), 'TOOL_MISSING', '没有可以采集目标矿石的可用工具。');
    ensure(safeRemoval(read, target, start), 'UNSAFE_MINING_TARGET', '矿石邻近液体、坠落方块、未加载区域或脚下支撑。');
  }
  const valid = (p, removed) => {
    const floor = read(p.offset(0, -1, 0));
    return full(floor) && !DANGER.test(floor.name) && !FALLING.test(floor.name) &&
      !removed.has(key(p.offset(0, -1, 0))) && !p.offset(0, -1, 0).equals(target) &&
      !Object.values(bot.entities ?? {}).some(e => e !== bot.entity && e?.isValid === true &&
        !e.getDroppedItem && e.position && Math.abs(e.position.x - p.x - 0.5) < 0.8 &&
        Math.abs(e.position.z - p.z - 0.5) < 0.8 && e.position.y < p.y + 2 && e.position.y + (e.height ?? 1.8) > p.y);
  };
  const reached = (p, removed, standing = p.offset(0.5, 0, 0.5)) => purpose === 'pickup'
    ? distance(standing, target.offset(0.5, 0, 0.5)) <= 0.8
    : !(Math.max(Math.abs(target.x - p.x), Math.abs(target.z - p.z)) <= 1 && target.y < p.y) &&
      // 与基础 blockAt 的交互距离合同一致；露出近侧表面不代表方块中心也在操作范围内。
      distance(standing.offset(0, 1.5, 0), target.offset(0.5, 0.5, 0.5)) <= 4.5 &&
      sees(bot, read, standing, target, removed);
  if (valid(start, new Set()) && reached(start, new Set(), bot.entity.position)) {
    return state('completed', { reachable: true, next: { action: 'ready' }, expanded: 0, clearCount: 0 });
  }
  const queue = [{ p: start, cost: 0, removed: new Set(), actions: [] }], best = new Map([[key(start), 0]]);
  let expanded = 0;
  while (queue.length && expanded < 512) {
    ctx.check();
    queue.sort((x, y) => x.cost - y.cost);
    const current = queue.shift();
    if (current.cost !== best.get(key(current.p))) continue;
    expanded++;
    if (current.actions.length && reached(current.p, current.removed)) {
      return state('completed', { reachable: true, next: current.actions[0],
        expanded, clearCount: current.removed.size, steps: current.actions.length });
    }
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) for (const dy of [0, 1, -1]) {
      const p = current.p.offset(dx, dy, dz);
      if (distance(start, p) > radius || Math.abs(p.y - start.y) > 4 || !valid(p, current.removed)) continue;
      const removed = new Set(current.removed), actions = [...current.actions];
      // 上台阶还需要清除当前头顶；先头后脚，不能削掉目的地的支撑。
      const cells = dy > 0 ? [current.p.offset(0, 2, 0), p.offset(0, 1, 0), p] : [p.offset(0, 1, 0), p];
      let cost = current.cost + 1 + Math.abs(dy), allowed = true;
      for (const cell of cells) {
        const block = read(cell), k = key(cell);
        if (!block || DANGER.test(block.name)) { allowed = false; break; }
        if (air(block) || removed.has(k)) continue;
        if (cell.equals(target) || cell.y < current.p.y || !safeTerrain(bot, read, cell, start, removed)) { allowed = false; break; }
        removed.add(k);
        if (removed.size > maximum) { allowed = false; break; }
        const tool = items(bot).find(i => block.canHarvest(i.type));
        const digTime = typeof block.digTime === 'function' ? block.digTime(tool?.type) : 1000;
        cost += 5 + Math.min(20, Math.max(0, digTime / 1000));
        actions.push({ action: 'dig', position: plainPosition(cell), block_name: block.name });
      }
      if (!allowed) continue;
      actions.push({ action: 'move', position: plainPosition(p) });
      if (cost >= (best.get(key(p)) ?? Infinity)) continue;
      best.set(key(p), cost);
      queue.push({ p, cost, removed, actions });
    }
  }
  return state('completed', { reachable: false, next: null, expanded,
    reason: expanded >= 512 ? 'SEARCH_LIMIT' : 'NO_SAFE_ROUTE', truncated: expanded >= 512 });
}

module.exports = function registerVein(registry, { digBlock }) {
  registry.register({ name: 'inspect_vein', description: '只读检查矿石种子及六面相邻的同类、已暴露矿石；不穿透扫描整片矿脉。', mutates: false,
    parameters: object({ position, block_name: text }),
    execute(ctx, a) {
      ctx.check();
      const { bot } = ctx, origin = vector(a.position);
      ensure(isOre(a.block_name) && bot.registry.blocksByName[a.block_name], 'NOT_ORE', '目标不是当前版本已注册的矿石。');
      ensure(distance(bot.entity.position, origin) <= 16, 'OUT_OF_RANGE', '只检查16格内矿脉邻接信息。');
      const blocks = [];
      for (const p of [origin, ...DIRECTIONS.map(d => origin.offset(...d))]) {
        const block = bot.blockAt(p);
        if (block?.name !== a.block_name) continue;
        if (!DIRECTIONS.some(d => air(bot.blockAt(p.offset(...d))))) continue;
        blocks.push({ name: block.name, position: plainPosition(p), visible: Boolean(bot.canSeeBlock(block)) ||
          sees(bot, q => bot.blockAt(q), bot.entity.position, p, new Set(), 16) });
      }
      return state('completed', { blocks, scanned: 7, source: 'exposed_neighbors' });
    }
  });
  const parameters = object({ position, block_name: text, purpose: { type: 'string', enum: ['mine', 'pickup'] },
    radius: number(1, 16, true), max_clear_blocks: number(0, 32, true), tool_name: text }, ['position']);
  registry.register({ name: 'inspect_mining_route', description: '只读规划有限矿脉通道：最多512个节点，天然支撑、两格空间，按距离与挖掘成本选择下一步；不会修改世界。',
    parameters, mutates: false, execute: planRoute });
  registry.register({ name: 'mine_route_block', description: '重新规划并只挖下一块已校验的通道障碍；复用基础挖掘，不开全局挖掘、不搭块。实际方块移除才确认成功。',
    parameters,
    async execute(ctx, a) {
      const route = planRoute(ctx, a);
      ensure(route.reachable && route.next?.action === 'dig', 'ROUTE_CHANGED', '通道下一步已变化，请重新诊断。');
      const next = route.next;
      const result = await digBlock(ctx, { position: next.position, block_name: next.block_name }, {
        visible: block => {
          const read = p => ctx.bot.blockAt(p);
          ensure(safeTerrain(ctx.bot, read, block.position, ctx.bot.entity.position.floored(), new Set()),
            'UNSAFE_MINING_TARGET', '挖掘前通道风险已变化。');
          return sees(ctx.bot, read, ctx.bot.entity.position, block.position);
        }, digFace: 'raycast',
      });
      return { ...result, clearedBlock: next, target: a.position };
    }
  });
  registry.register({ name: 'mine_vein_block', description: '矿脉 Skill 的单矿石操作：重新检查液体、坠落物及支撑，再复用基础挖掘与工具选择。',
    parameters: object({ position, block_name: text, tool_name: text }, ['position', 'block_name']),
    async execute(ctx, a) {
      const route = planRoute(ctx, { ...a, purpose: 'mine', max_clear_blocks: 0 });
      ensure(route.reachable && route.next?.action === 'ready', 'ORE_NOT_REACHABLE', '矿石尚未处于可操作位置。');
      return digBlock(ctx, a, {
        visible: block => {
          const read = p => ctx.bot.blockAt(p);
          ensure(safeRemoval(read, block.position, ctx.bot.entity.position.floored()), 'UNSAFE_MINING_TARGET', '挖掘前矿石风险已变化。');
          return sees(ctx.bot, read, ctx.bot.entity.position, block.position);
        },
        digFace: 'raycast',
      });
    }
  });
};
