const { validPosition } = require('./playerState');

function classifyEntity(entity) {
  if (entity.type === 'player') return 'player';
  const kind = typeof entity.kind === 'string' ? entity.kind.toLowerCase() : '';
  if (kind.includes('hostile')) return 'hostile';
  if (kind.includes('passive') || kind.includes('animal')) return 'passive';
  if (entity.type === 'hostile') return 'hostile';
  return 'other';
}

function readNearbyEntities(bot, ready, radius, maximum) {
  if (!ready || !validPosition(bot.entity?.position) || !bot.entities || typeof bot.entities !== 'object') {
    return { available: false, items: [], truncated: false };
  }

  const origin = bot.entity.position;
  const candidates = [];
  for (const [id, entity] of Object.entries(bot.entities)) {
    if (!entity || entity === bot.entity || entity.id === bot.entity.id) continue;
    if (entity.isValid !== true || bot.entities[id] !== entity || !validPosition(entity.position)) continue;
    const dx = entity.position.x - origin.x;
    const dy = entity.position.y - origin.y;
    const dz = entity.position.z - origin.z;
    const distance = Math.hypot(dx, dy, dz);
    if (distance > radius) continue;

    candidates.push({
      id: Number.isFinite(entity.id) ? entity.id : Number(id),
      name: entity.username || entity.name || entity.displayName || 'unknown',
      type: entity.type || 'other',
      category: classifyEntity(entity),
      position: { x: entity.position.x, y: entity.position.y, z: entity.position.z },
      distance,
      isPlayer: entity.type === 'player',
      valid: true
    });
  }
  candidates.sort((a, b) => a.distance - b.distance || a.id - b.id);
  return {
    available: true,
    items: candidates.slice(0, maximum),
    truncated: candidates.length > maximum
  };
}

module.exports = { readNearbyEntities, classifyEntity };
