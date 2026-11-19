const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { Vec3 } = require('vec3');
const registry = require('prismarine-registry')('1.21.4');
const { pathfinder, goals: { GoalFollow, GoalNear } } = require('mineflayer-pathfinder');
const { createMovementController, LIMITS } = require('../src/movement');

function setup(t, { ownerUsername = '', pathfinding, realPlugin = false, spawn = true, monitor } = {}) {
  const bot = new EventEmitter();
  const calls = [];
  const logs = [];
  const notifications = [];
  bot.registry = registry;
  bot.game = { minY: -64, height: 384 };
  bot.entity = { id: 1, position: new Vec3(0.5, 64, 0.5), velocity: new Vec3(0, 0, 0), onGround: true, effects: {} };
  bot.players = { Steve: { entity: { id: 2, position: new Vec3(10, 64, 0), isValid: true } } };
  bot.entities = {};
  bot.inventory = { items: () => [{ type: registry.itemsByName.dirt.id, count: 64 }] };
  bot.controlState = { forward: false, back: false, left: false, right: false, jump: false, sprint: false, sneak: false };
  bot.jumpQueued = false;
  bot.setControlState = (key, value) => {
    if (key === 'jump' && value && !bot.controlState.jump) bot.jumpQueued = true;
    bot.controlState[key] = value;
  };
  bot.getControlState = (key) => bot.controlState[key];
  bot.clearControlStates = () => { calls.push(['clear']); Object.keys(bot.controlState).forEach((key) => { bot.controlState[key] = false; }); };
  bot.loadPlugin = (plugin) => {
    calls.push(['loadPlugin', plugin]);
    if (realPlugin) {
      plugin(bot);
    } else {
      bot.pathfinder = {
        goal: null,
        setGoal(goal, dynamic = false) {
          this.goal = goal;
          calls.push(['setGoal', goal, dynamic]);
          bot.emit('goal_updated', goal, dynamic);
        },
        setMovements(movements) { this.movements = movements; }
      };
      if (monitor) bot.on('physicsTick', () => monitor(bot));
    }
  };
  const movement = createMovementController(bot, {
    ownerUsername, pathfinding,
    logger: { log: (line) => logs.push(line), error: (line) => logs.push(line) }
  });
  movement.on('result', (result) => notifications.push(result));
  t.after(() => movement.dispose());
  if (spawn) bot.emit('spawn');
  return { bot, movement, calls, logs, notifications };
}

const flushEvents = () => new Promise((resolve) => queueMicrotask(resolve));

test('移动终态可按原 ID 查询：到达、失败、替换、停止与断线均不误报', async (t) => {
  const { bot, movement } = setup(t);
  const first = movement.goTo('Steve', 3, 64, 0);
  assert.equal(movement.getTask(first.taskId).status, 'running');
  const firstGoal = bot.pathfinder.goal;
  bot.emit('goal_reached', firstGoal);
  await flushEvents();
  assert.equal(movement.getTask(first.taskId).status, 'arrived');
  const following = movement.follow('Steve');
  const next = movement.goTo('Steve', 4, 64, 0);
  bot.emit('goal_reached', firstGoal); await flushEvents();
  assert.equal(movement.getTask(following.taskId).status, 'cancelled');
  assert.equal(movement.getTask(next.taskId).status, 'running');
  bot.emit('path_update', { status: 'noPath' }); await flushEvents();
  assert.equal(movement.getTask(next.taskId).status, 'no_path');
  const stopped = movement.goTo('Steve', 5, 64, 0);
  movement.stop('Steve');
  assert.equal(movement.getTask(stopped.taskId).status, 'cancelled');
  const ended = movement.follow('Steve'); bot.emit('end');
  assert.equal(movement.getTask(ended.taskId).status, 'cancelled');
  const copy = movement.getTask(first.taskId); copy.status = 'fake';
  assert.equal(movement.getTask(first.taskId).status, 'arrived');
});

test('移动历史有上限；死亡和维度切换取消历史任务，保留旧 getState 结构', (t) => {
  const { bot, movement } = setup(t);
  const first = movement.follow('Steve');
  for (let i = 0; i < 130; i++) movement.goTo('Steve', 5, 64, 0);
  assert.equal(movement.getTask(first.taskId), null);
  const current = movement.getState().taskId;
  bot.emit('respawn');
  assert.equal(movement.getTask(current).status, 'cancelled');
  assert.deepEqual(movement.getState(), { type: 'idle', username: null, taskId: null });
});

test('工具等待复用原移动控制器的到达事件，不改变本地导航或泄漏监听器', async (t) => {
  const { bot, movement } = setup(t);
  const { createToolSystem } = require('../src/tools');
  const tools = createToolSystem(bot, { movement, logger: { log() {}, error() {} } });
  tools.guardMovement();
  t.after(() => tools.dispose());
  bot.emit('spawn');
  const before = movement.listenerCount('result');
  const pending = tools.execute('goto_position', { x: 2, y: 64, z: 0 }, 'Steve');
  const goal = bot.pathfinder.goal;
  assert.ok(goal instanceof GoalNear);
  assert.equal(movement.listenerCount('result'), before + 1);
  bot.emit('goal_reached', goal);
  const result = await pending;
  assert.equal(result.data.status, 'completed');
  assert.equal(result.data.movement.status, 'arrived');
  assert.equal(movement.getState().type, 'idle');
  assert.equal(movement.listenerCount('result'), before);
  assert.equal(bot.pathfinder.movements.canDig, false);
});

test('插件仅加载一次；每次进入世界都应用禁止挖掘、放置的配置', (t) => {
  const { bot, calls } = setup(t);
  const listeners = bot.listenerCount('path_update');
  bot.emit('spawn');
  assert.equal(calls.filter(([name]) => name === 'loadPlugin').length, 1);
  assert.equal(calls.find(([name]) => name === 'loadPlugin')[1], pathfinder);
  assert.equal(bot.listenerCount('path_update'), listeners);
  const moves = bot.pathfinder.movements;
  assert.equal(moves.canDig, false);
  assert.equal(moves.allow1by1towers, false);
  assert.equal(moves.allowParkour, false);
  assert.equal(moves.canOpenDoors, false);
  assert.equal(moves.maxDropDown, 1);
  assert.equal(moves.infiniteLiquidDropdownDistance, false);
  assert.deepEqual(moves.scafoldingBlocks, []);
  assert.equal(moves.countScaffoldingItems(), 0);
  assert.equal(moves.exclusionPlace({}), Infinity);
  assert.equal(moves.safeToBreak({}), false);
  assert.equal(bot.pathfinder.thinkTimeout, 5000);
  assert.equal(bot.pathfinder.searchRadius, 256);
});

test('跟随发送者实体，动态目标随玩家移动更新', (t) => {
  const { bot, movement, calls } = setup(t);
  const result = movement.follow('Steve');
  assert.equal(result.ok, true);
  assert.equal(movement.getState().type, 'follow');
  const goal = bot.pathfinder.goal;
  assert.ok(goal instanceof GoalFollow);
  assert.equal(goal.entity, bot.players.Steve.entity);
  assert.equal(goal.rangeSq, 9);
  assert.equal(calls.at(-1)[2], true);
  bot.players.Steve.entity.position.x = 20;
  assert.equal(goal.hasChanged(), true);
  assert.equal(goal.x, 20);
});

test('玩家实体不可见或过远时明确拒绝，不取消当前有效任务', (t) => {
  const { bot, movement } = setup(t);
  movement.goTo('Steve', 10, 64, 10);
  const previous = bot.pathfinder.goal;
  assert.equal(movement.follow('Alex').status, 'missing_player');
  bot.players.Steve.entity.position.x = 1000;
  assert.equal(movement.follow('Steve').status, 'out_of_range');
  assert.equal(bot.pathfinder.goal, previous);
});

test('停止立即清除动态跟随或坐标目标和所有主动移动控制', (t) => {
  const { bot, movement } = setup(t);
  for (const start of [() => movement.follow('Steve'), () => movement.goTo('Steve', 20, 64, 0)]) {
    start();
    Object.keys(bot.controlState).forEach((key) => { bot.controlState[key] = true; });
    bot.jumpQueued = true;
    const result = movement.stop('Steve');
    assert.equal(result.ok, true);
    assert.equal(result.message, '已停止移动');
    assert.equal(bot.pathfinder.goal, null);
    assert.deepEqual(movement.getState(), { type: 'idle', username: null, taskId: null });
    assert.ok(Object.values(bot.controlState).every((value) => value === false));
    assert.equal(bot.jumpQueued, false);
  }
  assert.equal(movement.stop('Steve').ok, true);
});

test('坐标使用 GoalNear 和向下取整；到达后只反馈一次', async (t) => {
  const { bot, movement, notifications } = setup(t);
  assert.equal(movement.goTo('Steve', -12.1, 65.9, 20).ok, true);
  const goal = bot.pathfinder.goal;
  assert.ok(goal instanceof GoalNear);
  assert.deepEqual([goal.x, goal.y, goal.z, goal.rangeSq], [-13, 65, 20, 4]);
  bot.emit('goal_reached', goal);
  bot.emit('goal_reached', goal);
  await flushEvents();
  assert.equal(movement.getState().type, 'idle');
  assert.equal(notifications.length, 1);
  assert.equal(notifications[0].status, 'arrived');
  assert.match(notifications[0].message, /-13 65 20/);
});

test('每项任务有唯一 taskId，旧任务迟到的结果不会改变或误报新任务', async (t) => {
  const { bot, movement, notifications } = setup(t);
  const oldTask = movement.goTo('Steve', 20, 64, 0);
  const oldGoal = bot.pathfinder.goal;
  bot.emit('path_update', { status: 'noPath', context: { goal: oldGoal } });
  const newTask = movement.follow('Steve');
  assert.ok(Number.isSafeInteger(oldTask.taskId));
  assert.ok(Number.isSafeInteger(newTask.taskId));
  assert.notEqual(oldTask.taskId, newTask.taskId);
  const newGoal = bot.pathfinder.goal;
  bot.emit('goal_reached', oldGoal);
  bot.emit('path_update', { status: 'noPath', context: { goal: oldGoal } });
  await flushEvents();
  assert.equal(bot.pathfinder.goal, newGoal);
  assert.equal(movement.getState().taskId, newTask.taskId);
  assert.deepEqual(notifications, []);
});

test('可独立配置目标距离、插件搜索范围和单次搜索时限', (t) => {
  const pathfinding = { maxDistance: 384, searchRadius: 512, thinkTimeout: 7000 };
  const { bot, movement } = setup(t, { pathfinding });
  assert.equal(bot.pathfinder.searchRadius, 512);
  assert.equal(bot.pathfinder.thinkTimeout, 7000);
  bot.players.Steve.entity.position = new Vec3(300, 64, 0);
  assert.equal(movement.follow('Steve').ok, true);
  const oldTask = movement.getState().taskId;
  assert.equal(movement.goTo('Steve', 300, 64, 0).ok, true);
  assert.notEqual(movement.getState().taskId, oldTask);
  assert.equal(movement.goTo('Steve', 385, 64, 0).status, 'out_of_range');
  assert.match(movement.goTo('Steve', 385, 64, 0).message, /超出本次导航范围/);
});

test('校验有限数字、世界边界、维度高度和单次导航距离', (t) => {
  const { bot, movement } = setup(t);
  movement.follow('Steve');
  const goal = bot.pathfinder.goal;
  for (const coordinates of [
    [NaN, 64, 0], [0, Infinity, 0], [0, 64, -Infinity], ['10', 64, 0],
    [30000000, 64, 0], [0, -65, 0], [0, 319, 0], [1000, 64, 0]
  ]) {
    assert.equal(movement.goTo('Steve', ...coordinates).ok, false);
    assert.equal(bot.pathfinder.goal, goal);
  }
  bot.game = { minY: 0, height: 128 };
  assert.equal(movement.goTo('Steve', 0, -1, 0).ok, false);
  assert.equal(movement.goTo('Steve', 0, 127, 0).ok, false);
  assert.equal(movement.goTo('Steve', 0, 126, 0).ok, true);
  delete bot.game;
  assert.equal(movement.goTo('Steve', 0, 64, 0).status, 'not_ready');
});

test('新目标替换旧目标；旧到达与排队失败回调不能取消新目标', async (t) => {
  const { bot, movement, notifications } = setup(t);
  movement.goTo('Steve', 20, 64, 0);
  const oldGoal = bot.pathfinder.goal;
  bot.emit('path_update', { status: 'noPath' });
  movement.follow('Steve');
  const newGoal = bot.pathfinder.goal;
  bot.emit('goal_reached', oldGoal);
  await flushEvents();
  assert.equal(bot.pathfinder.goal, newGoal);
  assert.equal(movement.getState().type, 'follow');
  assert.equal(notifications.length, 0);
  movement.goTo('Steve', 5, 64, 5);
  assert.notEqual(bot.pathfinder.goal, newGoal);
  assert.equal(movement.getState().type, 'goto');
});

test('停止优先于排队的到达或失败反馈，不会被旧事件恢复', async (t) => {
  const { bot, movement, notifications } = setup(t);
  movement.goTo('Steve', 10, 64, 0);
  bot.emit('goal_reached', bot.pathfinder.goal);
  bot.emit('path_update', { status: 'timeout' });
  movement.stop('Steve');
  await flushEvents();
  assert.equal(bot.pathfinder.goal, null);
  assert.equal(notifications.length, 0);
});

test('无路径或搜索超时停止，正常 partial 不误判失败', async (t) => {
  const { bot, movement, notifications } = setup(t);
  for (const status of ['noPath', 'timeout']) {
    movement.goTo('Steve', 10, 64, 0);
    bot.emit('path_update', { status: 'partial' });
    await flushEvents();
    assert.equal(movement.getState().type, 'goto');
    bot.emit('path_update', { status });
    await flushEvents();
    assert.equal(movement.getState().type, 'idle');
    assert.equal(bot.pathfinder.goal, null);
    assert.equal(notifications.at(-1).ok, false);
  }
});

test('正常重算不停止；卡住和执行错误停止，避免无限重试', async (t) => {
  const { bot, movement } = setup(t);
  for (const failure of ['stuck', 'dig_error', 'place_error', 'no_scaffolding_blocks']) {
    movement.follow('Steve');
    for (const reason of ['goal_moved', 'block_updated', 'chunk_loaded']) bot.emit('path_reset', reason);
    await flushEvents();
    assert.equal(movement.getState().type, 'follow');
    bot.emit('path_reset', failure);
    await flushEvents();
    assert.equal(movement.getState().type, 'idle');
  }
});

test('跟随目标不可见或离开游戏时停止', async (t) => {
  const { bot, movement, notifications } = setup(t);
  movement.follow('Steve');
  bot.emit('entityGone', bot.players.Steve.entity);
  await flushEvents();
  assert.equal(notifications.at(-1).status, 'lost_target');
  assert.equal(bot.pathfinder.goal, null);
  movement.follow('Steve');
  bot.emit('playerLeft', { username: 'Steve' });
  await flushEvents();
  assert.equal(movement.getState().type, 'idle');
});

test('主人限制在移动工具中执行，未授权停止不会取消主人目标', (t) => {
  const { bot, movement } = setup(t, { ownerUsername: 'steve' });
  assert.equal(movement.follow('Steve').ok, true);
  const goal = bot.pathfinder.goal;
  assert.equal(movement.follow('Alex').status, 'forbidden');
  assert.equal(movement.goTo('Alex', 10, 64, 0).status, 'forbidden');
  assert.equal(movement.stop('Alex').status, 'forbidden');
  assert.equal(bot.pathfinder.goal, goal);
  assert.equal(movement.stop('Steve').ok, true);
});

test('启动前不接受移动；死亡或换维度清理状态，重生后可接新任务', (t) => {
  const { bot, movement, calls } = setup(t, { spawn: false });
  assert.equal(movement.follow('Steve').status, 'not_ready');
  bot.emit('spawn');
  for (const event of ['death', 'respawn']) {
    movement.follow('Steve');
    bot.emit(event);
    assert.equal(bot.pathfinder.goal, null);
    assert.equal(movement.getState().type, 'idle');
    assert.equal(movement.follow('Steve').status, 'not_ready');
    bot.emit('spawn');
    assert.equal(movement.goTo('Steve', 10, 64, 0).ok, true);
  }
  assert.equal(calls.filter(([name]) => name === 'loadPlugin').length, 1);
});

test('断开或被踢后清除任务、计时器及移动监听器，重复清理安全', async (t) => {
  const { bot, movement, notifications } = setup(t);
  movement.follow('Steve');
  bot.emit('path_update', { status: 'timeout' });
  bot.emit('kicked');
  bot.emit('end');
  movement.dispose();
  await flushEvents();
  assert.equal(bot.pathfinder.goal, null);
  assert.equal(movement.getState().type, 'idle');
  assert.equal(bot.listenerCount('path_update'), 0);
  assert.equal(bot.listenerCount('spawn'), 0);
  assert.equal(bot.listenerCount('entityGone'), 0);
  assert.equal(movement.listenerCount('result'), 0);
  assert.equal(notifications.length, 0);
  assert.equal(movement.follow('Steve').status, 'not_ready');
});

test('设置目标异常时清除任务并释放控制', (t) => {
  const { bot, movement } = setup(t);
  const original = bot.pathfinder.setGoal;
  bot.pathfinder.setGoal = function (goal, dynamic) {
    if (goal) throw new Error('failed');
    original.call(this, goal, dynamic);
  };
  bot.controlState.forward = true;
  assert.equal(movement.follow('Steve').ok, false);
  assert.equal(bot.pathfinder.goal, null);
  assert.equal(movement.getState().type, 'idle');
  assert.equal(bot.controlState.forward, false);
});

test('看门狗限制无进展任务，停止与断开会清除旧计时器', (t) => {
  t.mock.timers.enable({ apis: ['Date', 'setInterval'] });
  const { bot, movement, notifications } = setup(t);
  movement.follow('Steve');
  t.mock.timers.tick(LIMITS.stuckTimeout);
  assert.equal(notifications.at(-1).status, 'stuck');
  assert.equal(bot.pathfinder.goal, null);
  movement.goTo('Steve', 10, 64, 0);
  movement.stop('Steve');
  t.mock.timers.tick(LIMITS.navigationTimeout * 2);
  assert.equal(notifications.length, 1);
  movement.follow('Steve');
  bot.emit('end');
  t.mock.timers.tick(LIMITS.navigationTimeout * 2);
  assert.equal(notifications.length, 1);
});

test('坐标任务即使不断移动也受总时限约束；partial 不延长时限', (t) => {
  t.mock.timers.enable({ apis: ['Date', 'setInterval'] });
  const { bot, movement, notifications } = setup(t);
  movement.goTo('Steve', 100, 64, 0);
  for (let i = 0; i < 60; i += 1) {
    bot.entity.position.z += 1;
    bot.emit('path_update', { status: 'partial' });
    t.mock.timers.tick(1000);
  }
  assert.equal(notifications.at(-1).status, 'timeout');
  assert.equal(bot.pathfinder.goal, null);
});

test('跟随超过 60 秒仍持续运行；目标实体丢失或超出范围后停止', (t) => {
  t.mock.timers.enable({ apis: ['Date', 'setInterval'] });
  const { bot, movement, notifications } = setup(t);
  bot.players.Steve.entity.position = new Vec3(2, 64, 0);
  movement.follow('Steve');
  t.mock.timers.tick(120000);
  assert.equal(movement.getState().type, 'follow');
  assert.equal(notifications.length, 0);
  bot.players.Steve.entity.position.x = 1000;
  t.mock.timers.tick(1000);
  assert.equal(notifications.at(-1).status, 'out_of_range');
  bot.players.Steve.entity.position.x = 10;
  movement.follow('Steve');
  delete bot.players.Steve;
  t.mock.timers.tick(1000);
  assert.equal(notifications.at(-1).status, 'lost_target');
});

test('替换任务时清除旧任务的看门狗，监听器数量不随指令增加', (t) => {
  t.mock.timers.enable({ apis: ['Date', 'setInterval'] });
  const { bot, movement, notifications } = setup(t);
  const count = bot.listenerCount('goal_updated');
  movement.follow('Steve');
  t.mock.timers.tick(14000);
  movement.goTo('Steve', 10, 64, 0);
  t.mock.timers.tick(1000);
  assert.equal(movement.getState().type, 'goto');
  assert.equal(notifications.length, 0);
  for (let i = 0; i < 20; i += 1) {
    movement.follow('Steve');
    movement.goTo('Steve', 10, 64, 0);
  }
  assert.equal(bot.listenerCount('goal_updated'), count);
  assert.equal(bot.listenerCount('path_update'), 1);
});

test('外部目标替换时释放本模块任务，不清除外部新目标', (t) => {
  const { bot, movement, notifications } = setup(t);
  movement.follow('Steve');
  const otherGoal = new GoalNear(5, 64, 5, 1);
  bot.pathfinder.setGoal(otherGoal);
  assert.equal(movement.getState().type, 'idle');
  assert.equal(bot.pathfinder.goal, otherGoal);
  assert.equal(notifications.at(-1).status, 'cancelled');
  movement.stop('Steve');
  assert.equal(bot.pathfinder.goal, null);
});

// 使用实际插件、方块注册表和 A* 算法，只把游戏世界替换为可控的小块地形。
function setTerrain(bot, terrain) {
  const Block = require('prismarine-block')(bot.registry);
  bot.blockAt = (position) => {
    const p = position.floored();
    if (Math.abs(p.x) > 8 || Math.abs(p.z) > 8 || p.y < 60 || p.y > 70) return null;
    const name = terrain(p);
    const block = Block.fromStateId(bot.registry.blocksByName[name].defaultState, 0);
    block.position = p;
    return block;
  };
}

function computePath(bot, goal) {
  const search = bot.pathfinder.getPathFromTo(bot.pathfinder.movements, bot.entity.position, goal, {
    timeout: 1000, tickTimeout: 20
  });
  let result;
  for (const step of search) result = step.result;
  assert.ok(result);
  return result;
}

test('实际寻路器可绕过三格高障碍，生成的路径不挖掘、不放置', (t) => {
  const { bot } = setup(t, { realPlugin: true });
  setTerrain(bot, (p) => p.y < 64 || (p.x === 2 && p.z === 0 && p.y < 67) ? 'stone' : 'air');
  const goal = new GoalNear(5, 64, 0, 1);
  const result = computePath(bot, goal);
  assert.equal(result.status, 'success');
  assert.ok(goal.isEnd(result.path.at(-1)));
  assert.ok(result.path.some((node) => node.z !== 0));
  assert.ok(result.path.every((node) => node.toBreak.length === 0 && node.toPlace.length === 0));
});

test('实际寻路器在必须挖墙或搭路的地形报告无路，即使背包里有方块', (t) => {
  const { bot } = setup(t, { realPlugin: true });
  const goal = new GoalNear(5, 64, 0, 1);
  for (const terrain of [
    (p) => p.y < 64 || (p.x === 2 && p.y < 68) ? 'stone' : 'air',
    (p) => p.y < 64 && p.x !== 2 ? 'stone' : 'air'
  ]) {
    setTerrain(bot, terrain);
    const result = computePath(bot, goal);
    assert.equal(result.status, 'noPath');
    assert.ok(result.path.every((node) => node.toBreak.length === 0 && node.toPlace.length === 0));
  }
});

test('实际插件到达事件结束后才收尾，反馈回调中的新目标不会被覆盖', async (t) => {
  const { bot, movement, notifications } = setup(t, { realPlugin: true });
  setTerrain(bot, (p) => p.y < 64 ? 'stone' : 'air');
  movement.on('result', (result) => {
    if (result.status === 'arrived') movement.follow('Steve');
  });
  movement.goTo('Steve', 1, 64, 0);
  bot.emit('physicsTick');
  await flushEvents();
  assert.equal(notifications[0].status, 'arrived');
  assert.equal(movement.getState().type, 'follow');
  assert.ok(bot.pathfinder.goal instanceof GoalFollow);
});

test('实际插件报告无路后清除目标，下一帧不再规划', async (t) => {
  const { bot, movement, notifications } = setup(t, { realPlugin: true });
  setTerrain(bot, (p) => p.y < 64 || ((Math.abs(p.x) === 1 || Math.abs(p.z) === 1) && p.y < 68) ? 'stone' : 'air');
  movement.goTo('Steve', 6, 64, 0);
  bot.emit('physicsTick');
  await flushEvents();
  assert.equal(notifications[0].status, 'no_path');
  assert.equal(bot.pathfinder.goal, null);
  bot.emit('physicsTick');
  assert.equal(notifications.length, 1);
  assert.equal(bot.pathfinder.isMoving(), false);
});

test('跑步潜行模式可开关、组合并恢复；换目标保留模式与唯一 taskId', (t) => {
  const { bot, movement } = setup(t);
  assert.equal(movement.getMotionState().sprint, null);
  assert.equal(movement.setSprint('Steve', false).ok, true);
  assert.equal(bot.pathfinder.movements.allowSprinting, false);
  movement.setSneak('Steve', true);
  const first = movement.follow('Steve');
  assert.equal(bot.controlState.sneak, true);
  movement.setSprint('Steve', true);
  assert.equal(bot.pathfinder.movements.allowSprinting, false);
  assert.equal(movement.getState().taskId, first.taskId);
  movement.setSneak('Steve', false);
  assert.equal(bot.pathfinder.movements.allowSprinting, true);
  assert.equal(bot.controlState.sneak, false);
  movement.setSprint('Steve', false);
  movement.setSneak('Steve', true);
  movement.setSneak('Steve', false);
  assert.equal(bot.pathfinder.movements.allowSprinting, false);
  const second = movement.goTo('Steve', 7, 64, 0);
  assert.notEqual(second.taskId, first.taskId);
  assert.equal(movement.getMotionState().sprint, false);
  assert.equal(bot.controlState.forward, false); // 模式不会自行走路。
  movement.stop('Steve');
  assert.deepEqual(movement.getMotionState(), { sprint: null, sneak: false, jumping: false, allowSprinting: true });
  bot.emit('physicsTick');
  assert.ok(Object.values(bot.controlState).every((value) => value === false));
  movement.setSneak('Steve', true);
  const setGoal = bot.pathfinder.setGoal;
  bot.pathfinder.setGoal = () => { throw new Error('simulated cancellation failure'); };
  assert.equal(movement.follow('Steve').ok, false);
  assert.equal(movement.getMotionState().sneak, false);
  assert.equal(bot.controlState.sneak, false);
  bot.pathfinder.setGoal = setGoal;
});

test('移动模式与单次跳跃执行主人权限、就绪和严格参数校验', async (t) => {
  const { bot, movement } = setup(t, { ownerUsername: 'Steve', spawn: false });
  assert.equal(movement.setSprint('Steve', true).status, 'not_ready');
  assert.equal((await movement.jump('Steve')).status, 'not_ready');
  bot.emit('spawn');
  assert.equal(movement.setSneak('Alex', true).status, 'forbidden');
  assert.equal((await movement.jump('Alex')).status, 'forbidden');
  assert.equal(movement.setSprint('Steve', 'true').status, 'invalid');
  for (const field of ['isInWater', 'isInLava', 'airborne']) {
    bot.entity.onGround = field !== 'airborne';
    bot.entity.isInWater = field === 'isInWater';
    bot.entity.isInLava = field === 'isInLava';
    assert.equal((await movement.jump('Steve')).status, 'not_grounded');
  }
});

test('模式在寻路按键重写后保持；手动跳跃只脉冲一帧且不覆盖寻路需要的跳跃', async (t) => {
  let pathJump = false;
  const { bot, movement } = setup(t, { monitor: (b) => {
    b.setControlState('sneak', false);
    b.setControlState('sprint', true);
    b.setControlState('jump', pathJump);
  } });
  movement.setSneak('Steve', true);
  const task = movement.follow('Steve');
  const pending = movement.jump('Steve');
  assert.equal((await movement.jump('Steve')).status, 'busy');
  bot.emit('physicsTick');
  assert.equal(bot.controlState.jump, true);
  assert.equal(bot.controlState.sneak, true);
  assert.equal(bot.controlState.sprint, false);
  bot.entity.onGround = false;
  bot.entity.position.y += 0.4;
  pathJump = true;
  bot.emit('physicsTick');
  assert.equal(bot.controlState.jump, true); // 前钩子释放，寻路器本帧重新要求跳跃。
  pathJump = false;
  bot.emit('physicsTick');
  assert.equal(bot.controlState.jump, false);
  bot.entity.onGround = true;
  bot.entity.position.y = 64;
  bot.emit('physicsTick');
  assert.equal((await pending).status, 'jumped');
  assert.equal(movement.getState().taskId, task.taskId);
  assert.equal(movement.getMotionState().sneak, true);
});

test('停止及生命周期变化取消跳跃和排队起跳；没有残留模式或重复监听器', async (t) => {
  for (const event of ['stop', 'death', 'respawn', 'kicked', 'end']) {
    const { bot, movement } = setup(t);
    const before = bot.listenerCount('physicsTick');
    movement.setSprint('Steve', true);
    movement.setSneak('Steve', true);
    movement.follow('Steve');
    const pending = movement.jump('Steve');
    bot.emit('physicsTick');
    assert.equal(bot.jumpQueued, true);
    if (event === 'stop') movement.stop('Steve');
    else bot.emit(event);
    assert.equal((await pending).ok, false);
    assert.equal(bot.jumpQueued, false);
    assert.equal(bot.pathfinder.goal, null);
    assert.equal(movement.getMotionState().sneak, false);
    assert.equal(movement.getMotionState().sprint, null);
    assert.ok(Object.values(bot.controlState).every((v) => v === false));
    if (['end', 'kicked'].includes(event)) assert.equal(bot.listenerCount('physicsTick'), 0);
    else {
      bot.emit('spawn');
      assert.equal(bot.listenerCount('physicsTick'), before);
    }
  }
});

test('跳跃有界且不能把未离地当作完成；超时、AbortSignal 和按键异常清理任务', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const cause of ['timeout', 'abort', 'exception']) {
    const { bot, movement } = setup(t);
    movement.setSneak('Steve', true);
    movement.follow('Steve');
    const abort = new AbortController();
    const pending = movement.jump('Steve', { signal: abort.signal });
    bot.emit('physicsTick');
    assert.equal(movement.getMotionState().jumping, true);
    if (cause === 'timeout') t.mock.timers.tick(3000);
    else if (cause === 'abort') abort.abort();
    else {
      bot.setControlState = () => { throw new Error('simulated control failure'); };
      bot.emit('physicsTick');
    }
    assert.equal((await pending).ok, false);
    assert.equal(movement.getState().type, 'idle');
    assert.equal(movement.getMotionState().jumping, false);
    assert.equal(movement.getMotionState().sneak, false);
  }
});

test('寻路失败清理模式，正常到达保留模式直到显式关闭；旧回调不影响新模式', async (t) => {
  const { bot, movement } = setup(t);
  movement.setSneak('Steve', true);
  movement.goTo('Steve', 7, 64, 0);
  const oldGoal = bot.pathfinder.goal;
  bot.emit('path_update', { status: 'noPath', context: { goal: oldGoal } });
  movement.follow('Steve');
  await flushEvents();
  assert.equal(movement.getMotionState().sneak, true);
  bot.emit('path_update', { status: 'timeout', context: { goal: bot.pathfinder.goal } });
  await flushEvents();
  assert.equal(movement.getMotionState().sneak, false);
  movement.setSneak('Steve', true);
  movement.goTo('Steve', 7, 64, 0);
  bot.emit('goal_reached', bot.pathfinder.goal);
  await flushEvents();
  assert.equal(movement.getMotionState().sneak, true);
  assert.equal(bot.controlState.sneak, true);
  movement.setSneak('Steve', false);
  assert.equal(bot.controlState.sneak, false);
});

test('注册工具在导航等待时安全切换模式，保留等待锁和取消归属；严格布尔 Schema', async (t) => {
  const { bot, movement } = setup(t);
  const { createToolSystem } = require('../src/tools');
  const tools = createToolSystem(bot, { movement, logger: { log() {}, error() {} } });
  tools.guardMovement();
  t.after(() => tools.dispose());
  bot.emit('spawn');
  for (const bad of [1, 0, 'true', null]) {
    assert.equal((await tools.execute('set_sprint', { enabled: bad }, 'Steve')).error.code, 'INVALID_ARGUMENT');
  }
  const navigation = tools.execute('goto_position', { x: 7, y: 64, z: 0 }, 'Steve', 'nav');
  const taskId = movement.getState().taskId;
  const epoch = tools.getState().generation;
  assert.equal((await tools.execute('set_sneak', { enabled: true }, 'Steve')).success, true);
  assert.equal(movement.setSprint('Steve', true).ok, true);
  assert.equal(tools.getState().active, 'goto_position');
  assert.equal(tools.getState().generation, epoch);
  assert.equal(movement.getState().taskId, taskId);
  const stop = await tools.execute('stop_movement', {}, 'Steve');
  assert.equal(stop.success, true);
  assert.equal((await navigation).success, false);
  assert.equal(movement.getMotionState().sneak, false);
  assert.equal(tools.getState().active, null);
});

test('跳跃工具与原跟随共享任务；模式不能插入其它修改工具，取消清理锁和模式', async (t) => {
  const { bot, movement } = setup(t);
  const { createToolSystem } = require('../src/tools');
  const { object } = require('../src/tools/schema');
  const tools = createToolSystem(bot, { movement, logger: { log() {}, error() {} } });
  tools.guardMovement();
  t.after(() => tools.dispose());
  bot.emit('spawn');
  await tools.execute('follow_player', {}, 'Steve', 'follow');
  const taskId = movement.getState().taskId;
  const pending = tools.execute('jump', {}, 'Steve', 'jump1');
  bot.emit('physicsTick');
  assert.equal(movement.getState().taskId, taskId);
  assert.equal((await tools.execute('set_sneak', { enabled: true }, 'Steve')).error.code, 'BUSY');
  tools.cancel('Steve', false, 'follow');
  assert.equal((await pending).success, false);
  assert.equal(movement.getState().type, 'idle');
  let release;
  tools.register({ name: 'wait_test', parameters: object(), execute: () => new Promise((resolve) => { release = resolve; }) });
  const other = tools.execute('wait_test', {}, 'Steve');
  assert.equal((await tools.execute('set_sprint', { enabled: true }, 'Steve')).error.code, 'BUSY');
  assert.equal(movement.setSneak('Steve', true).status, 'busy');
  assert.equal((await movement.jump('Steve')).status, 'busy');
  release({ status: 'completed' });
  await other;
});

test('工具层跳跃超时、取消及断线均释放锁并停止原导航，不会迟到恢复模式', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { createToolSystem } = require('../src/tools');
  for (const cause of ['timeout', 'cancel', 'death', 'end']) {
    const { bot, movement } = setup(t);
    const tools = createToolSystem(bot, { movement, timeoutMs: 25, logger: { log() {}, error() {} } });
    tools.guardMovement();
    t.after(() => tools.dispose());
    bot.emit('spawn');
    await tools.execute('set_sneak', { enabled: true }, 'Steve');
    await tools.execute('goto_position', { x: 7, y: 64, z: 0, wait_seconds: 0 }, 'Steve', 'goto');
    const pending = tools.execute('jump', {}, 'Steve', 'jump');
    bot.emit('physicsTick');
    if (cause === 'timeout') t.mock.timers.tick(25);
    else if (cause === 'cancel') tools.cancel('Steve', false, 'jump');
    else bot.emit(cause);
    const result = await pending;
    assert.equal(result.success, false);
    if (cause === 'timeout') assert.equal(result.error.code, 'TIMEOUT');
    await flushEvents();
    assert.equal(tools.getState().active, null);
    assert.equal(movement.getState().type, 'idle');
    assert.equal(movement.getMotionState().sneak, false);
    assert.equal(bot.jumpQueued, false);
    t.mock.timers.tick(4000);
    assert.equal(movement.getMotionState().jumping, false);
  }
});

// 实际 pathfinder + prismarine-physics 集成：不替换 A* 或物理算法，只提供有限的内存地形。
function enablePhysics(bot) {
  const { Physics, PlayerState } = require('prismarine-physics');
  bot.version = '1.21.4';
  bot.jumpTicks = 0;
  bot.food = 20;
  bot.inventory.slots = [];
  Object.assign(bot.entity, { yaw: 0, pitch: 0, attributes: {} });
  bot.look = async (yaw, pitch) => { bot.entity.yaw = yaw; bot.entity.pitch = pitch; };
  const world = { getBlock: (p) => bot.blockAt(p) };
  bot.physics = Physics(require('minecraft-data')(bot.version), world);
  return async () => {
    bot.physics.simulatePlayer(new PlayerState(bot, bot.controlState), world).apply(bot);
    bot.emit('physicsTick');
    await flushEvents();
  };
}

test('实际寻路与物理模拟：跑步/潜行导航不产生第二目标，关闭潜行恢复速度策略', async (t) => {
  const distances = {};
  for (const mode of ['walk', 'sprint', 'sneak', 'restored']) {
    const { bot, movement } = setup(t, { realPlugin: true });
    setTerrain(bot, (p) => p.y < 64 ? 'stone' : 'air');
    const tick = enablePhysics(bot);
    movement.setSprint('Steve', mode !== 'walk');
    if (['sneak', 'restored'].includes(mode)) movement.setSneak('Steve', true);
    const started = movement.goTo('Steve', 7, 64, 0);
    for (let i = 0; i < 12; i++) await tick();
    distances[mode] = bot.entity.position.x;
    assert.equal(movement.getState().taskId, started.taskId);
    assert.equal(bot.pathfinder.movements.canDig, false);
    if (['sneak', 'restored'].includes(mode)) {
      assert.equal(bot.controlState.sneak, true);
      assert.equal(bot.controlState.sprint, false);
    }
    if (mode === 'restored') {
      movement.setSneak('Steve', false);
      assert.equal(bot.pathfinder.movements.allowSprinting, true);
    }
    for (let i = 0; i < 150 && movement.getState().type !== 'idle'; i++) await tick();
    assert.equal(movement.getState().type, 'idle');
    assert.ok(bot.entity.position.distanceTo(new Vec3(7, 64, 0)) < 2.5);
  }
  assert.ok(distances.sprint > distances.walk, JSON.stringify(distances));
  assert.ok(distances.walk > distances.sneak, JSON.stringify(distances));
});

test('实际物理模拟确认单次跳跃起落；跟随目标在跳跃和模式开关期间不变', async (t) => {
  const { bot, movement } = setup(t, { realPlugin: true });
  setTerrain(bot, (p) => p.y < 64 ? 'stone' : 'air');
  const tick = enablePhysics(bot);
  const tools = require('../src/tools').createToolSystem(bot, { movement, logger: { log() {}, error() {} } });
  tools.guardMovement();
  t.after(() => tools.dispose());
  bot.emit('spawn');
  bot.players.Steve.entity.position = new Vec3(2, 64, 0);
  const task = (await tools.execute('follow_player', {}, 'Steve', 'follow')).data.movement;
  await tools.execute('set_sneak', { enabled: true }, 'Steve');
  const pending = tools.execute('jump', {}, 'Steve', 'jump');
  let high = 64;
  for (let i = 0; i < 45; i++) {
    await tick();
    high = Math.max(high, bot.entity.position.y);
  }
  const completed = await pending;
  assert.equal(completed.success, true);
  assert.equal(completed.data.status, 'completed');
  assert.equal(completed.data.movement.status, 'jumped');
  assert.ok(high > 64.4);
  assert.equal(bot.entity.onGround, true);
  assert.equal(bot.controlState.jump, false);
  assert.equal(movement.getState().taskId, task.taskId);
  assert.equal(bot.controlState.sneak, true);
  tools.cancel('Steve', false, 'follow'); // 跳跃完成仍保留原跟随任务的取消归属。
  for (let i = 0; i < 5; i++) await tick();
  assert.ok(Object.values(bot.controlState).every((v) => v === false));
});

test('实际移动中的动态跟随可叠加单次跳跃，玩家改变方向后继续寻路', async (t) => {
  const { bot, movement } = setup(t, { realPlugin: true });
  setTerrain(bot, (p) => p.y < 64 ? 'stone' : 'air');
  const tick = enablePhysics(bot);
  bot.players.Steve.entity.position = new Vec3(7, 64, 0);
  movement.setSprint('Steve', true);
  const started = movement.follow('Steve');
  for (let i = 0; i < 5; i++) await tick();
  assert.ok(bot.entity.position.x > 0.5);
  const pending = movement.jump('Steve');
  let high = 64;
  for (let i = 0; i < 45; i++) {
    if (i === 10) bot.players.Steve.entity.position = new Vec3(7, 64, 5);
    await tick();
    high = Math.max(high, bot.entity.position.y);
  }
  assert.equal((await pending).status, 'jumped');
  assert.ok(high > 64.4);
  assert.equal(movement.getState().taskId, started.taskId);
  for (let i = 0; i < 60; i++) await tick();
  assert.ok(bot.entity.position.distanceTo(bot.players.Steve.entity.position) < 4);
  assert.equal(movement.getState().type, 'follow');
});
