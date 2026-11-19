const { object, position, number } = require('../schema');
const { ensure, vector, distance, plainPosition, state } = require('../common');

const hazard = /lava|water|fire|cactus|magma|campfire|berry_bush|powder_snow|portal/;
const air = (block) => block && ['air', 'cave_air', 'void_air'].includes(block.name);

// 有界地形/射线查询，不临时移动实体、不转头、不设置路径。候选站位不等于可达路径。
module.exports = function registerStanding(registry) {
  registry.register({ name: 'find_interaction_positions', description: '只读查找目标周围可站立且视线通畅的候选脚部坐标，不自动移动、不保证路径可达。导航后必须重新检查距离与视线；不能挖候选脚下支撑。', mutates: false,
    parameters: object({ position, radius: number(1, 4, true), max_results: number(1, 16, true) }, ['position']),
    execute(ctx, a) {
      const { bot } = ctx;
      const origin = vector(a.position), center = origin.offset(0.5, 0.5, 0.5);
      ensure(distance(bot.entity.position, origin) <= 16, 'OUT_OF_RANGE', '仅查询 Bot 周围 16 格内目标的站位。');
      ensure(Number.isFinite(bot.game?.minY) && Number.isFinite(bot.game?.height), 'NOT_READY', '维度高度尚不可用。');
      const minY = bot.game.minY, maxY = minY + bot.game.height;
      ensure(origin.y >= minY && origin.y < maxY, 'OUT_OF_RANGE', '目标超出维度高度。');
      ensure(bot.blockAt(origin), 'CHUNK_UNAVAILABLE', '目标区块尚未加载。');
      ensure(typeof bot.world?.raycast === 'function', 'NOT_READY', '射线查询尚不可用。');
      const radius = a.radius ?? 3, maximum = a.max_results ?? 8, candidates = [];
      let scanned = 0;
      for (let x = -radius; x <= radius; x++) for (let y = -2; y <= 2; y++) for (let z = -radius; z <= radius; z++) {
        ctx.check();
        scanned++;
        const p = origin.offset(x, y, z);
        if (p.y <= minY || p.y + 1 >= maxY || Math.abs(p.x) > 29999984 || Math.abs(p.z) > 29999984) continue;
        if (p.x === origin.x && p.z === origin.z && p.y - 1 === origin.y) continue;
        const feet = bot.blockAt(p), head = bot.blockAt(p.offset(0, 1, 0)), floor = bot.blockAt(p.offset(0, -1, 0));
        if (!air(feet) || !air(head) || !floor || floor.boundingBox !== 'block' || hazard.test(floor.name)) continue;
        // 全方块支撑保守处理，不把半砖/栅栏等部分碰撞形状当作完整平台。
        if (!floor.shapes?.some((shape) => shape.length === 6 && shape.every((v, i) => v === (i < 3 ? 0 : 1)))) continue;
        const standing = p.offset(0.5, 0, 0.5), eye = standing.offset(0, 1.62, 0), reach = distance(eye, center);
        if (reach > 4.5) continue;
        const blocked = Object.values(bot.entities ?? {}).some((entity) => entity !== bot.entity && entity?.isValid !== false && entity?.position &&
          Math.abs(entity.position.x - standing.x) < 0.8 && Math.abs(entity.position.z - standing.z) < 0.8 &&
          entity.position.y < standing.y + 2 && entity.position.y + (entity.height ?? 1.8) > standing.y);
        if (blocked) continue;
        const direction = center.minus(eye).normalize();
        // world.raycast 会跳过未加载方块；先检查射线经过的区块，未知不能当作空气。
        let loaded = true;
        for (let d = 0; d < reach; d += 0.25) {
          if (!bot.blockAt(eye.plus(direction.scaled(d)))) { loaded = false; break; }
        }
        if (!loaded) continue;
        const hit = bot.world.raycast(eye, direction, reach + 0.01, (block, iterator) =>
          Boolean(iterator.intersect(block.shapes, block.position)) || block.position.equals(origin));
        if (!hit?.position?.equals(origin)) continue;
        candidates.push({ position: plainPosition(standing), distance: distance(bot.entity.position, standing), interactionDistance: reach });
      }
      candidates.sort((x, y) => x.distance - y.distance);
      return state('completed', { candidates: candidates.slice(0, maximum), truncated: candidates.length > maximum,
        scanned, pathVerified: false, dimension: bot.game.dimension ?? null, timestamp: Date.now() });
    }
  });
};
