const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { startBot, describeError, formatReason } = require('../src/bot');

function setup(auth = 'offline') {
  const bot = new EventEmitter();
  // 实际游戏名可能与 Microsoft 缓存标识不同，必须按 bot.username 过滤。
  bot.username = 'ActualBotName';
  bot.health = 18;
  bot.food = 15;
  bot.entity = { position: { x: -12.25, y: 64, z: 9.5 } };
  bot.game = { dimension: 'overworld' };
  bot.experience = { level: 8 };
  bot.isAlive = true;
  const sent = [];
  const logs = [];
  const errors = [];
  const calls = [];
  const moveCalls = [];
  const movement = new EventEmitter();
  const perception = {
    getWorldSnapshot: () => ({
      timestamp: Date.now(),
      availability: {
        player: bot.entity ? 'available' : 'unavailable', inventory: bot.entity ? 'available' : 'unavailable',
        nearbyEntities: bot.entity ? 'available' : 'unavailable', nearbyBlocks: bot.entity ? 'available' : 'unavailable'
      },
      player: {
        position: bot.entity?.position ?? null, health: bot.health ?? null, food: bot.food ?? null,
        experienceLevel: bot.experience?.level ?? null, dimension: bot.game?.dimension ?? null,
        alive: bot.isAlive ?? null, movement: { active: false, type: null, taskId: null }
      },
      inventory: [{ name: 'oak_log', count: 4, slot: 9 }],
      equipment: [],
      nearbyEntities: [{ id: 2, name: 'Cow', category: 'passive', distance: 3, isPlayer: false }],
      nearbyBlocks: [{ name: 'stone', position: { x: 1, y: 64, z: 1 }, distance: 1.4 }],
      truncated: { entities: false, blocks: false }
    })
  };
  movement.follow = (username) => { moveCalls.push(['follow', username]); return { message: '开始跟随' }; };
  movement.stop = (username) => { moveCalls.push(['stop', username]); return { message: '已停止移动' }; };
  movement.goTo = (...args) => { moveCalls.push(['goto', ...args]); return { message: '开始前往' }; };
  let failures = 0;
  bot.chat = (message) => sent.push(message);
  const result = startBot({ host: '127.0.0.1', port: 51234, username: 'MineMate', auth }, {
    createBot: (options) => { calls.push(options); return bot; },
    createMovement: (instance) => { assert.equal(instance, bot); return movement; },
    createPerceptionModule: (instance) => { assert.equal(instance, bot); return perception; },
    logger: { log: (message) => logs.push(message), error: (message) => errors.push(message) },
    onFailure: () => { failures += 1; }
  });
  return { bot, result, sent, logs, errors, calls, movement, perception, moveCalls, failures: () => failures };
}

test('传递指定认证与端口；首次进入游戏欢迎一次', () => {
  for (const auth of ['offline', 'microsoft']) {
    const state = setup(auth);
    assert.equal(state.result, state.bot);
    assert.equal(state.bot.perception, state.perception);
    assert.equal(state.calls[0].auth, auth);
    assert.equal(state.calls[0].port, 51234);
    assert.equal(state.calls[0].hideErrors, true);
    assert.equal('version' in state.calls[0], false);
    state.bot.emit('spawn');
    state.bot.emit('spawn');
    assert.equal(state.sent.length, 1);
    assert.match(state.sent[0], /MineMate/);
    assert.equal(state.logs.filter((line) => line.includes('成功进入游戏')).length, 1);
  }
});

test('忽略自身消息，玩家问候正常响应，普通消息仅记录', () => {
  const state = setup('microsoft');
  state.bot.emit('spawn');
  state.bot.emit('chat', 'ActualBotName', '你好');
  state.bot.emit('chat', 'ActualBotName', '状态');
  assert.equal(state.sent.length, 1);
  state.bot.emit('chat', 'Steve', ' 你好 ');
  assert.match(state.sent[1], /你好，Steve/);
  state.bot.emit('chat', 'Steve', '随便聊聊');
  assert.equal(state.sent.length, 2);
  assert.ok(state.logs.includes('[Chat] Steve: 随便聊聊'));
});

test('状态使用每次请求时的实时数值，保留负坐标', () => {
  const state = setup();
  state.bot.emit('spawn');
  state.bot.emit('chat', 'Steve', '状态');
  assert.match(state.sent[1], /生命值：18\/20，饥饿值：15\/20，位置：X=-12.25 Y=64.00 Z=9.50/);
  state.bot.health = 12;
  state.bot.food = 7;
  state.bot.entity.position.x = 42;
  state.bot.emit('chat', 'Steve', '状态');
  assert.match(state.sent[2], /生命值：12\/20，饥饿值：7\/20，位置：X=42.00/);
  assert.match(state.sent[2], /维度：overworld，经验等级：8，存活/);
});

test('进入世界前和断开后不回复；未就绪状态有提示', () => {
  const state = setup();
  state.bot.emit('chat', 'Steve', '状态');
  assert.equal(state.sent.length, 0);
  state.bot.emit('spawn');
  state.bot.entity = undefined;
  state.bot.emit('chat', 'Steve', '状态');
  assert.match(state.sent[1], /尚未就绪/);
  state.bot.emit('end', 'socketClosed');
  state.bot.emit('chat', 'Steve', '你好');
  assert.equal(state.sent.length, 2);
  assert.match(state.logs.at(-2), /连接已断开/);
});

test('聊天发送异常记录错误，不让事件回调崩溃', () => {
  const state = setup();
  state.bot.chat = () => { throw new Error('not in play state'); };
  assert.doesNotThrow(() => state.bot.emit('spawn'));
  assert.doesNotThrow(() => state.bot.emit('chat', 'Steve', '你好'));
  assert.equal(state.errors.length, 2);
  assert.ok(state.errors.every((line) => line.includes('聊天发送失败')));
});

test('连接错误、登录拒绝和断开均记录，不重连且失败只通知一次', () => {
  const state = setup();
  state.bot.emit('error', Object.assign(new Error('refused'), { code: 'ECONNREFUSED' }));
  state.bot.emit('kicked', { text: 'Failed to verify username!' }, false);
  state.bot.emit('end', 'socketClosed');
  assert.match(state.errors.join('\n'), /MC_PORT/);
  assert.match(state.errors.join('\n'), /登录被服务器拒绝.*Failed to verify username/);
  assert.match(state.errors.join('\n'), /Offline 无法连接要求正版认证/);
  assert.match(state.logs.join('\n'), /不会自动重连/);
  assert.equal(state.failures(), 1);
  assert.equal(state.calls.length, 1);
});

test('进入游戏后被踢也记录原因及失败状态', () => {
  const state = setup('microsoft');
  state.bot.emit('spawn');
  state.bot.emit('kicked', 'Duplicate login', true);
  state.bot.emit('end', 'socketClosed');
  assert.match(state.errors[0], /被服务器踢出：Duplicate login/);
  assert.equal(state.failures(), 1);
});

test('Microsoft 认证错误不回显原始令牌，也不降级为 Offline', () => {
  const state = setup('microsoft');
  state.bot.emit('error', new Error('Microsoft authentication invalid_grant access_token=secret-test-token'));
  assert.match(state.errors.join('\n'), /Microsoft 认证失败/);
  assert.doesNotMatch(state.errors.join('\n'), /secret-test-token/);
  assert.equal(state.calls.length, 1);
  assert.equal(state.calls[0].auth, 'microsoft');
  assert.equal(state.failures(), 1);
});

test('同步创建异常与进入世界前的静默断开都会报告失败', () => {
  const errors = [];
  let failed = 0;
  const result = startBot({ host: '127.0.0.1', port: 51234, username: 'MineMate', auth: 'offline' }, {
    createBot: () => { throw new Error('Unsupported protocol version'); },
    logger: { log: () => {}, error: (line) => errors.push(line) },
    onFailure: () => { failed += 1; }
  });
  assert.equal(result, null);
  assert.equal(failed, 1);
  assert.match(errors[0], /版本或协议不兼容/);
  const state = setup();
  state.bot.emit('end');
  assert.equal(state.failures(), 1);
  assert.match(state.logs.at(-1), /未提供原因/);
});

test('识别网络与未知错误，未知认证错误不泄露响应内容', () => {
  for (const [code, expected] of [
    ['ENOTFOUND', /无法解析主机/], ['ETIMEDOUT', /连接超时/], ['ECONNRESET', /连接中断/]
  ]) {
    assert.match(describeError(new AggregateError([Object.assign(new Error('network'), { code })]), 'microsoft'), expected);
  }
  assert.doesNotMatch(describeError(new Error('secret-test-body'), 'microsoft'), /secret-test-body/);
});

test('服务器原因保持可读，并隐藏常见凭据字段', () => {
  assert.match(formatReason({ text: 'Server closed' }), /Server closed/);
  const reason = formatReason('access_token=secret-a refresh_token=secret-b password=secret-c Authorization: Bearer secret-d\nnext');
  assert.doesNotMatch(reason, /secret-|\n/);
  assert.match(reason, /已隐藏/);
});

test('移动聊天指令传递发送者及坐标，复用已有 Bot', () => {
  const state = setup();
  state.bot.emit('spawn');
  state.bot.emit('chat', 'Steve', '跟随我');
  state.bot.emit('chat', 'Alex', '前往 -12.5 65 20');
  state.bot.emit('chat', 'Steve', '停止');
  assert.deepEqual(state.moveCalls, [['follow', 'Steve'], ['goto', 'Alex', -12.5, 65, 20], ['stop', 'Steve']]);
  assert.equal(state.sent.at(-1), '已停止移动');
  assert.equal(state.calls.length, 1);
  state.bot.emit('chat', 'ActualBotName', '跟随我');
  assert.equal(state.moveCalls.length, 3);
});

test('错误坐标格式提示用法；非法数字交给移动工具校验', () => {
  const state = setup();
  state.bot.emit('spawn');
  for (const command of ['前往', '前往 1 2', '前往 1 2 3 4']) {
    state.bot.emit('chat', 'Steve', command);
    assert.match(state.sent.at(-1), /用法/);
  }
  assert.equal(state.moveCalls.length, 0);
  state.bot.emit('chat', 'Steve', '前往 abc 65 20');
  assert.equal(Number.isNaN(state.moveCalls[0][2]), true);
});

test('移动异步反馈只注册一次，断开后不再发送', () => {
  const state = setup();
  state.bot.emit('spawn');
  for (let i = 0; i < 10; i += 1) state.bot.emit('chat', 'Steve', '跟随我');
  assert.equal(state.movement.listenerCount('result'), 1);
  state.movement.emit('result', { message: '已到达目标附近' });
  assert.equal(state.sent.at(-1), '已到达目标附近');
  state.bot.emit('end', 'socketClosed');
  const count = state.sent.length;
  assert.equal(state.movement.listenerCount('result'), 0);
  state.movement.emit('result', { message: '不应发送' });
  assert.equal(state.sent.length, count);
});

test('意外移动异常会尝试取消任务并给出反馈', () => {
  const state = setup();
  state.bot.emit('spawn');
  state.movement.follow = () => { throw new Error('unexpected'); };
  assert.doesNotThrow(() => state.bot.emit('chat', 'Steve', '跟随我'));
  assert.deepEqual(state.moveCalls, [['stop', 'Steve']]);
  assert.match(state.sent.at(-1), /处理移动指令失败/);
});

test('背包、附近和环境命令展示感知摘要并限制聊天长度', () => {
  const state = setup();
  state.bot.emit('spawn');
  state.bot.emit('chat', 'Steve', '背包');
  assert.match(state.sent.at(-1), /oak_log×4/);
  state.bot.emit('chat', 'Steve', '附近');
  assert.match(state.sent.at(-1), /Cow（被动生物，3.0格）/);
  state.bot.emit('chat', 'Steve', '环境');
  assert.match(state.sent.at(-1), /stone \[1, 64, 1\]/);
  assert.ok(state.sent.every((message) => message.length < 256));
});

test('感知数据不可用时反馈清晰，不影响移动指令', () => {
  const state = setup();
  state.bot.emit('spawn');
  state.perception.getWorldSnapshot = () => ({ availability: {}, player: {}, inventory: [], nearbyEntities: [], nearbyBlocks: [] });
  for (const command of ['状态', '背包', '附近', '环境']) {
    state.bot.emit('chat', 'Steve', command);
    assert.match(state.sent.at(-1), /暂不可用/);
  }
  state.bot.emit('chat', 'Steve', '跟随我');
  assert.deepEqual(state.moveCalls, [['follow', 'Steve']]);
});

test('感知聊天摘要汇总同名物品，并按长名称和大坐标的实际长度截断', () => {
  const state = setup();
  state.bot.emit('spawn');
  const snapshot = state.perception.getWorldSnapshot();
  snapshot.inventory = [{ name: 'oak_log', count: 4, slot: 9 }, { name: 'oak_log', count: 5, slot: 10 }];
  state.perception.getWorldSnapshot = () => snapshot;
  state.bot.emit('chat', 'Steve', '背包');
  assert.match(state.sent.at(-1), /oak_log×9/);
  snapshot.nearbyBlocks = Array.from({ length: 8 }, (_, index) => ({
    name: 'deepslate_iron_ore', position: { x: -29999984 + index, y: -60, z: -29999984 }, distance: index + 1
  }));
  state.bot.emit('chat', 'Steve', '环境');
  assert.ok(state.sent.at(-1).length <= 240);
  assert.match(state.sent.at(-1), /结果已截断/);
  snapshot.nearbyEntities = Array.from({ length: 8 }, () => ({ name: 'a'.repeat(48), distance: 2, category: 'other' }));
  state.bot.emit('chat', 'Steve', '附近');
  assert.ok(state.sent.at(-1).length <= 240);
  assert.match(state.sent.at(-1), /结果已截断/);
});

test('移动插件加载失败有明确日志，原有聊天仍可使用', () => {
  const bot = new EventEmitter();
  const sent = [];
  const errors = [];
  let failures = 0;
  bot.username = 'MineMate';
  bot.chat = (message) => sent.push(message);
  startBot({ host: '127.0.0.1', port: 51234, username: 'MineMate', auth: 'offline' }, {
    createBot: () => bot,
    createMovement: () => { throw new Error('load failed'); },
    logger: { log: () => {}, error: (line) => errors.push(line) },
    onFailure: () => { failures += 1; }
  });
  bot.emit('spawn');
  bot.emit('chat', 'Steve', '你好');
  assert.match(sent.at(-1), /你好，Steve/);
  bot.emit('chat', 'Steve', '跟随我');
  assert.match(sent.at(-1), /尚未就绪/);
  assert.match(errors[0], /移动插件加载失败/);
  assert.equal(failures, 1);
});
