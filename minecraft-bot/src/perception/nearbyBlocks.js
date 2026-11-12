const RESOURCE_BLOCKS = Object.freeze([
  'oak_log', 'birch_log', 'spruce_log', 'stone', 'cobblestone', 'dirt', 'grass_block',
  'sand', 'water', 'coal_ore', 'iron_ore', 'deepslate_coal_ore', 'deepslate_iron_ore'
]);

const { validPosition } = require('./playerState');

function scanNearbyBlocks(bot, position, radius, maximum) {
  const registry = bot.registry?.blocksByName;
  if (typeof bot.findBlocks !== 'function' || typeof bot.blockAt !== 'function' || !registry) {
    return { available: false, items: [], truncated: false };
  }
  if (!bot.blockAt(position, false)) return { available: false, items: [], truncated: false };

  const ids = RESOURCE_BLOCKS.map((name) => registry[name]?.id).filter(Number.isFinite);
  if (ids.length === 0) return { available: true, items: [], truncated: false };

  // 官方 findBlocks 会向下取整搜索中心，补偿这段偏移后再按真实距离筛选。
  // 有限立方体上界确保取回全部候选，再按 Bot 的真实距离排序、截断。
  const offset = Math.hypot(position.x - Math.floor(position.x), position.y - Math.floor(position.y), position.z - Math.floor(position.z));
  const searchRadius = radius + offset;
  const candidateLimit = (2 * Math.ceil(searchRadius) + 1) ** 3;
  const points = bot.findBlocks({
    point: position,
    matching: ids,
    maxDistance: searchRadius,
    count: candidateLimit,
    useExtraInfo: false
  });
  const found = Array.isArray(points) ? points : [];
  const origin = position;
  const items = found.flatMap((point) => {
    if (!validPosition(point)) return [];
    const block = bot.blockAt(point, false);
    const distance = Math.hypot(point.x - origin.x, point.y - origin.y, point.z - origin.z);
    // findBlocks 以脚下整数方块为搜索中心；再次过滤实际距离，保证不越过感知半径。
    if (!block || !RESOURCE_BLOCKS.includes(block.name) || distance > radius) return [];
    return [{
      name: block.name,
      position: { x: point.x, y: point.y, z: point.z },
      distance
    }];
  });
  items.sort((a, b) => a.distance - b.distance);
  return { available: true, items: items.slice(0, maximum), truncated: items.length > maximum };
}

function readNearbyBlocks(bot, { ready, position, dimension, radius, maximum, cacheMs, now = Date.now(), cache }) {
  if (!ready || bot.isAlive === false || !validPosition(position)) {
    cache.value = null;
    return { available: false, items: [], truncated: false };
  }

  const currentDimension = typeof dimension === 'string' ? dimension : null;
  const previous = cache.value;
  // 缓存仅供同一精确位置复用，包括方块内部的小幅移动也会重新扫描。
  const reusable = previous && previous.dimension === currentDimension &&
    previous.position.x === position.x && previous.position.y === position.y && previous.position.z === position.z &&
    now >= previous.scannedAt && now - previous.scannedAt < cacheMs;

  if (!reusable) {
    cache.value = null;
    const scanned = scanNearbyBlocks(bot, position, radius, maximum);
    if (!scanned.available) return scanned;
    cache.value = {
      dimension: currentDimension,
      position: { x: position.x, y: position.y, z: position.z },
      scannedAt: now,
      items: scanned.items.map((item) => ({ name: item.name, position: { ...item.position } })),
      truncated: scanned.truncated
    };
    return scanned;
  }

  return {
    available: true,
    items: previous.items.map((item) => ({
      name: item.name,
      position: { ...item.position },
      distance: Math.hypot(item.position.x - position.x, item.position.y - position.y, item.position.z - position.z)
    })),
    truncated: previous.truncated
  };
}

module.exports = { readNearbyBlocks, scanNearbyBlocks, RESOURCE_BLOCKS };
