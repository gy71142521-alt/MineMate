function validPosition(position) {
  return [position?.x, position?.y, position?.z].every(Number.isFinite);
}

function numberOrNull(value) {
  return Number.isFinite(value) ? value : null;
}

function readPlayerState(bot, movement, ready, logger = console) {
  const position = ready && validPosition(bot.entity?.position)
    ? { x: bot.entity.position.x, y: bot.entity.position.y, z: bot.entity.position.z }
    : null;

  let movementState = null;
  if (ready && typeof movement?.getState === 'function') {
    try {
      const state = movement.getState();
      movementState = {
        active: typeof state?.type === 'string' ? state.type !== 'idle' : null,
        type: typeof state?.type === 'string' && state.type !== 'idle' ? state.type : null,
        taskId: Number.isSafeInteger(state?.taskId) ? state.taskId : null
      };
    } catch {
      logger.error('[Perception] 移动状态读取失败。');
      movementState = { active: null, type: null, taskId: null };
    }
  }

  return {
    position,
    health: ready ? numberOrNull(bot.health) : null,
    food: ready ? numberOrNull(bot.food) : null,
    experienceLevel: ready ? numberOrNull(bot.experience?.level) : null,
    dimension: ready && typeof bot.game?.dimension === 'string' ? bot.game.dimension : null,
    alive: ready && typeof bot.isAlive === 'boolean' ? bot.isAlive : null,
    movement: movementState
  };
}

module.exports = { readPlayerState, validPosition };
