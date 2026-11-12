const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { Vec3 } = require('vec3');
const registry = require('prismarine-registry')('1.21.4');
const { pathfinder, goals: { GoalFollow, GoalNear } } = require('mineflayer-pathfinder');
const { createMovementController, LIMITS } = require('../src/movement');

function setup(t, { ownerUsername = '', pathfinding, realPlugin = false, spawn = true } = {}) {
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
    const result = movement.stop('Steve');
    assert.equal(result.ok, true);
    assert.equal(result.message, '已停止移动');
    assert.equal(bot.pathfinder.goal, null);
    assert.deepEqual(movement.getState(), { type: 'idle', username: null, taskId: null });
    assert.ok(Object.values(bot.controlState).every((value) => value === false));
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
