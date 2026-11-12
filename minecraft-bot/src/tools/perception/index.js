const { DEFAULT_PERCEPTION } = require('../../config');
const { readPlayerState, validPosition } = require('./playerState');
const { readInventory, countItem } = require('./inventory');
const { readNearbyEntities } = require('./nearbyEntities');
const { readNearbyBlocks } = require('./nearbyBlocks');

function createPerception(bot, { movement, config = {}, logger = console } = {}) {
  const options = { ...DEFAULT_PERCEPTION, ...config };
  const blockCache = { value: null };
  let ready = false;
  let disposed = false;
  let lastDimension = typeof bot.game?.dimension === 'string' ? bot.game.dimension : null;

  function invalidateBlocks() {
    blockCache.value = null;
  }

  function onSpawn() {
    ready = true;
    invalidateBlocks();
  }

  function onRespawn() {
    ready = false;
    invalidateBlocks();
  }

  function onDeath() {
    invalidateBlocks();
  }

  function onGame() {
    const dimension = typeof bot.game?.dimension === 'string' ? bot.game.dimension : null;
    if (dimension !== lastDimension) invalidateBlocks();
    lastDimension = dimension;
  }

  function onEnd() {
    ready = false;
    invalidateBlocks();
    dispose();
  }

  function safeRead(name, fallback, read) {
    try {
      return read();
    } catch {
      logger.error(`[Perception] ${name}读取失败。`);
      return fallback;
    }
  }

  function getPlayerState() {
    const state = safeRead('自身状态', null, () => readPlayerState(bot, movement, ready, logger));
    return state ?? {
      position: null, health: null, food: null, experienceLevel: null,
      dimension: null, alive: null, movement: null
    };
  }

  function getInventory() {
    const inventory = safeRead('背包', null, () => readInventory(bot, ready && validPosition(bot.entity?.position)));
    return inventory ?? { available: false, items: [], equipment: [] };
  }

  function getItemCount(name) {
    return safeRead('背包汇总', null, () => countItem(getInventory(), name));
  }

  function getNearbyEntities() {
    const entities = safeRead('附近实体', null, () => readNearbyEntities(bot, ready, options.entityRadius, options.maxEntities));
    return entities ?? { available: false, items: [], truncated: false };
  }

  function getNearbyBlocks() {
    const position = ready && validPosition(bot.entity?.position) ? bot.entity.position : null;
    const dimension = typeof bot.game?.dimension === 'string' ? bot.game.dimension : null;
    return safeRead('附近方块', { available: false, items: [], truncated: false }, () => readNearbyBlocks(bot, {
      ready,
      position,
      dimension,
      radius: options.blockRadius,
      maximum: options.maxBlocks,
      cacheMs: options.blockCacheMs,
      cache: blockCache
    }));
  }

  function getWorldSnapshot() {
    const player = getPlayerState();
    const inventory = getInventory();
    const nearbyEntities = getNearbyEntities();
    const nearbyBlocks = getNearbyBlocks();
    return {
      timestamp: Date.now(),
      availability: {
        player: player.position ? 'available' : 'unavailable',
        inventory: inventory.available ? 'available' : 'unavailable',
        nearbyEntities: nearbyEntities.available ? 'available' : 'unavailable',
        nearbyBlocks: nearbyBlocks.available ? 'available' : 'unavailable'
      },
      player,
      inventory: inventory.items,
      equipment: inventory.equipment,
      nearbyEntities: nearbyEntities.items,
      nearbyBlocks: nearbyBlocks.items,
      truncated: { entities: nearbyEntities.truncated, blocks: nearbyBlocks.truncated },
      limits: {
        entityRadius: options.entityRadius,
        maxEntities: options.maxEntities,
        blockRadius: options.blockRadius,
        maxBlocks: options.maxBlocks,
        blockCacheMs: options.blockCacheMs
      }
    };
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    ready = false;
    invalidateBlocks();
    for (const [event, handler] of Object.entries(listeners)) bot.removeListener(event, handler);
  }

  const listeners = {
    spawn: onSpawn,
    respawn: onRespawn,
    death: onDeath,
    game: onGame,
    blockUpdate: invalidateBlocks,
    chunkColumnLoad: invalidateBlocks,
    chunkColumnUnload: invalidateBlocks,
    end: onEnd,
    kicked: onEnd
  };

  for (const [event, handler] of Object.entries(listeners)) bot.on(event, handler);
  return {
    getPlayerState,
    getInventory,
    getItemCount,
    getNearbyEntities,
    getNearbyBlocks,
    getWorldSnapshot,
    dispose
  };
}

module.exports = { createPerception };
