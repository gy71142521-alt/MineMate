const { EventEmitter } = require('node:events');
const { pathfinder, Movements, goals: { GoalFollow, GoalNear } } = require('mineflayer-pathfinder');
const { DEFAULT_PATHFINDING } = require('./config');

const LIMITS = Object.freeze({
  followRange: 3,
  nearRange: 2,
  ...DEFAULT_PATHFINDING,
  navigationTimeout: 60000,
  stuckTimeout: 15000,
  checkInterval: 1000,
  worldBorder: 29999984
});

function createSafeMovements(bot) {
  const movements = new Movements(bot);
  movements.canDig = false;
  movements.scafoldingBlocks = [];
  movements.allow1by1towers = false;
  movements.exclusionAreasPlace.push(() => Infinity);
  movements.allowParkour = false;
  movements.allowFreeMotion = false;
  movements.canOpenDoors = false;
  movements.maxDropDown = 1;
  movements.infiniteLiquidDropdownDistance = false;
  return movements;
}

const validPosition = (p) => [p?.x, p?.y, p?.z].every(Number.isFinite);
const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
const sameName = (a, b) => a.toLowerCase() === b.toLowerCase();
let nextTaskId = 0;

// 所有移动工具复用同一实例；同步返回执行结果，异步结果通过 result 事件通知。
function createMovementController(bot, { ownerUsername = '', pathfinding = {}, logger = console } = {}) {
  const controller = new EventEmitter();
  const options = { ...DEFAULT_PATHFINDING, ...pathfinding };
  let task = null;
  let ready = false;
  let disposed = false;

  function result(ok, status, message, username, taskId = null) {
    return { ok, status, message, username, taskId };
  }

  function isCurrent(current) {
    return !disposed && current != null && task?.taskId === current.taskId;
  }

  function releaseControls() {
    let ok = true;
    try {
      if (bot.pathfinder) bot.pathfinder.setGoal(null);
    } catch {
      ok = false;
      logger.error('[Movement] 清除寻路目标失败。');
    }
    try {
      // 连接尚未完成时，Mineflayer 的物理插件可能还未注入。
      if (typeof bot.clearControlStates === 'function') bot.clearControlStates();
    } catch {
      ok = false;
      logger.error('[Movement] 释放移动控制失败。');
    }
    return ok;
  }

  function forgetTask() {
    const previous = task;
    // 必须先清除任务再触发插件事件，避免同步的 goal_updated 误判任务。
    task = null;
    if (previous) clearInterval(previous.timer);
    return previous;
  }

  function cancel() {
    forgetTask();
    return releaseControls();
  }

  function finish(current, ok, status, message) {
    if (!isCurrent(current)) return;
    if (!cancel()) {
      ok = false;
      status = 'failed';
      message = '移动任务已结束，但清理寻路控制失败，请检查终端日志。';
    }
    logger.log(`[Movement] ${message}`);
    controller.emit('result', result(ok, status, message, current.username, current.taskId));
  }

  function finishAfterEvent(current, ok, status, message) {
    // 插件在 path_update / goal_reached 发出后仍会更新路径，避免重入收尾被覆盖。
    queueMicrotask(() => finish(current, ok, status, message));
  }

  function checkAccess(username, needsReady = true) {
    if (typeof username !== 'string' || !username) {
      return result(false, 'invalid', '缺少移动指令发送者。', username);
    }
    if (ownerUsername && !sameName(username, ownerUsername)) {
      return result(false, 'forbidden', '只有配置的主人可以控制移动。', username);
    }
    if (disposed || (needsReady && (!ready || !validPosition(bot.entity?.position)))) {
      return result(false, 'not_ready', '移动系统尚未就绪，请等待 Bot 进入世界。', username);
    }
    return null;
  }

  function watch(current) {
    if (!isCurrent(current)) return;
    const position = bot.entity?.position;
    if (!validPosition(position)) {
      finish(current, false, 'failed', '无法读取 Bot 位置，已停止移动。');
      return;
    }
    const now = Date.now();
    if (current.type === 'follow') {
      const entity = bot.players?.[current.username]?.entity;
      if (entity !== current.entity || !validPosition(entity?.position)) {
        finish(current, false, 'lost_target', '跟随的玩家实体已不可见，已停止移动。');
        return;
      }
      const gap = distance(position, entity.position);
      if (gap > options.maxDistance) {
        finish(current, false, 'out_of_range', `玩家超出本次跟随范围（${options.maxDistance} 格），已停止移动。`);
        return;
      }
      // 动态跟随在目标附近等待是正常状态，不应误判为卡住。
      if (gap <= LIMITS.followRange + 1) current.lastProgressAt = now;
    }
    // 跟随没有总时限；60 秒只限制单次坐标导航。
    if (current.type === 'goto' && now - current.startedAt >= LIMITS.navigationTimeout) {
      finish(current, false, 'timeout', '前往坐标超过 60 秒，已停止；目标可能过远或无法到达。');
      return;
    }
    if (distance(position, current.checkpoint) >= 0.5) {
      current.checkpoint = { x: position.x, y: position.y, z: position.z };
      current.lastProgressAt = now;
    }
    if (now - current.lastProgressAt >= LIMITS.stuckTimeout) {
      finish(current, false, 'stuck', '连续 15 秒没有移动进展，已停止；请检查障碍或未加载的区块。');
    }
  }

  function start(username, type, goal, extra, message) {
    if (!cancel()) return result(false, 'failed', '无法取消原移动目标，请检查终端日志。', username);
    const current = {
      taskId: ++nextTaskId,
      username, type, goal, ...extra,
      startedAt: Date.now(), lastProgressAt: Date.now(),
      checkpoint: { x: bot.entity.position.x, y: bot.entity.position.y, z: bot.entity.position.z }
    };
    task = current;
    try {
      bot.pathfinder.setGoal(goal, type === 'follow');
      current.timer = setInterval(() => watch(current), LIMITS.checkInterval);
      current.timer.unref();
      logger.log(`[Movement] ${message}`);
      return result(true, type, message, username, current.taskId);
    } catch {
      cancel();
      logger.error('[Movement] 设置移动目标失败。');
      return result(false, 'failed', '设置移动目标失败，已取消移动。', username);
    }
  }

  function follow(username) {
    const denied = checkAccess(username);
    if (denied) return denied;
    const entity = bot.players?.[username]?.entity;
    if (!validPosition(entity?.position)) {
      return result(false, 'missing_player', '看不到你的游戏实体，请靠近 Bot 并确认处于同一维度。', username);
    }
    if (distance(bot.entity.position, entity.position) > options.maxDistance) {
      return result(false, 'out_of_range', `玩家超出本次跟随范围（${options.maxDistance} 格），请先靠近 Bot。`, username);
    }
    return start(username, 'follow', new GoalFollow(entity, LIMITS.followRange), { entity }, `正在跟随 ${username}，保持约 3 格距离。`);
  }

  function goTo(username, x, y, z) {
    const denied = checkAccess(username);
    if (denied) return denied;
    if (![x, y, z].every(Number.isFinite)) {
      return result(false, 'invalid', '坐标必须是三个有效数字，例如：前往 100 65 200。', username);
    }
    const target = { x: Math.floor(x), y: Math.floor(y), z: Math.floor(z) };
    const minY = bot.game?.minY;
    const height = bot.game?.height;
    if (!Number.isFinite(minY) || !Number.isFinite(height) || height < 2) {
      return result(false, 'not_ready', '当前维度高度尚未就绪，请稍后再试。', username);
    }
    // 为脚部与头部保留两格空间，兼容旧版世界和不同维度的高度。
    const maxY = minY + height - 2;
    if (Math.abs(target.x) > LIMITS.worldBorder || Math.abs(target.z) > LIMITS.worldBorder || target.y < minY || target.y > maxY) {
      return result(false, 'out_of_range', `坐标超出范围：X/Z 须在 ±${LIMITS.worldBorder} 内，脚部 Y 须在 ${minY}–${maxY} 内。`, username);
    }
    if (distance(bot.entity.position, target) > options.maxDistance) {
      return result(false, 'out_of_range', `目标超出本次导航范围（${options.maxDistance} 格），请缩短距离或调整 PATHFINDER_MAX_DISTANCE。`, username);
    }
    return start(username, 'goto', new GoalNear(target.x, target.y, target.z, LIMITS.nearRange), { target }, `正在前往 ${target.x} ${target.y} ${target.z} 附近。`);
  }

  function stop(username) {
    const denied = checkAccess(username, false);
    if (denied) return denied;
    const taskId = task?.taskId ?? null;
    const ok = cancel();
    logger.log('[Movement] 已取消当前移动任务');
    return result(ok, ok ? 'stopped' : 'failed', ok ? '已停止移动' : '停止移动时发生错误，请检查终端日志。', username, taskId);
  }

  function initialize() {
    if (disposed) return;
    ready = false;
    if (!cancel()) return;
    try {
      bot.pathfinder.setMovements(createSafeMovements(bot));
      bot.pathfinder.thinkTimeout = options.thinkTimeout;
      bot.pathfinder.searchRadius = options.searchRadius;
      ready = true;
      logger.log('[Movement] 寻路已就绪：禁止挖掘、放置方块和搭柱');
    } catch {
      logger.error('[Movement] 寻路初始化失败，移动指令已禁用；请检查游戏版本兼容性。');
    }
  }

  function onPathUpdate(update) {
    if (!task || bot.pathfinder.goal !== task.goal) return;
    // 发布版搜索结果的 context.goal 标识原搜索目标；迟到的旧搜索结果不能绑定到新任务。
    if (update.context?.goal && update.context.goal !== task.goal) return;
    if (update.status === 'noPath') {
      finishAfterEvent(task, false, 'no_path', '在本次搜索范围内未找到安全可达路径，已停止移动；可调整搜索范围，不会挖掘或搭路。');
    } else if (update.status === 'timeout') {
      finishAfterEvent(task, false, 'timeout', `路径计算超过 ${options.thinkTimeout} 毫秒，已停止；本次搜索超时不代表目标一定不可达。`);
    }
    // partial 是分片计算的正常状态，不能当作失败，也不能延长任务总时限。
  }

  function onGoalReached(goal) {
    if (!task || task.type !== 'goto' || task.goal !== goal) return;
    const { x, y, z } = task.target;
    finishAfterEvent(task, true, 'arrived', `已到达 ${x} ${y} ${z} 附近。`);
  }

  function onGoalUpdated(goal) {
    if (!task || task.goal === goal) return;
    const previous = forgetTask();
    logger.log('[Movement] 原移动目标已被其他操作替换');
    controller.emit('result', result(false, 'cancelled', '原移动目标已被其他操作替换。', previous.username, previous.taskId));
  }

  function onPathReset(reason) {
    if (task && ['stuck', 'dig_error', 'place_error', 'no_scaffolding_blocks'].includes(reason)) {
      finishAfterEvent(task, false, 'failed', '寻路执行受阻，已停止；请检查地形后重新发出指令。');
    }
  }

  function onEntityGone(entity) {
    if (task?.type === 'follow' && task.entity === entity) {
      finishAfterEvent(task, false, 'lost_target', '跟随的玩家实体已不可见，已停止移动。');
    }
  }

  function onPlayerLeft(player) {
    if (task?.type === 'follow' && sameName(task.username, player.username)) {
      finishAfterEvent(task, false, 'lost_target', '跟随的玩家已离开游戏，已停止移动。');
    }
  }

  function pause() {
    ready = false;
    cancel();
  }

  const listeners = {
    spawn: initialize, death: pause, respawn: pause,
    path_update: onPathUpdate, goal_reached: onGoalReached,
    goal_updated: onGoalUpdated, path_reset: onPathReset,
    entityGone: onEntityGone, playerLeft: onPlayerLeft,
    kicked: dispose, end: dispose
  };

  function dispose() {
    if (disposed) return;
    disposed = true;
    ready = false;
    cancel();
    for (const [event, handler] of Object.entries(listeners)) bot.removeListener(event, handler);
    controller.removeAllListeners();
  }

  Object.assign(controller, {
    follow, goTo, stop, dispose,
    getState: () => ({ type: task?.type ?? 'idle', username: task?.username ?? null, taskId: task?.taskId ?? null })
  });
  bot.loadPlugin(pathfinder);
  for (const [event, handler] of Object.entries(listeners)) bot.on(event, handler);
  return controller;
}

module.exports = { createMovementController, createSafeMovements, LIMITS };
