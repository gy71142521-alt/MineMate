const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { registerChatEvents } = require('../src/events/chat');
const { startBot } = require('../src/bot');
const flush = () => new Promise((resolve) => setImmediate(resolve));

function fixture(handler = async () => ({ intent: 'follow', arguments: {}, reply: '已识别，尚未执行' })) {
  const bot = new EventEmitter();
  bot.username = 'MineMate';
  const sent = [], requests = [], moves = [], errors = [], logs = [];
  bot.chat = (text) => sent.push(text);
  const movement = new EventEmitter();
  for (const name of ['follow', 'stop', 'goTo']) movement[name] = (...args) => { moves.push([name, ...args]); return { message: '移动旧指令正常' }; };
  const perception = { getWorldSnapshot: () => ({ availability: { player: 'available', inventory: 'available', nearbyEntities: 'available', nearbyBlocks: 'available' },
    player: { health: 20, food: 20, position: { x: 1, y: 64, z: 2 } }, inventory: [], nearbyEntities: [], nearbyBlocks: [] }) };
  const agent = { message: async (...args) => { requests.push(args); return handler(...args); } };
  registerChatEvents(bot, { log: (text) => logs.push(text), error: (text) => errors.push(text) }, movement, perception, agent);
  bot.emit('spawn');
  return { bot, sent, requests, moves, errors, logs };
}

test('自然语言异步转发并回复，follow/goto/stop 意图不会执行游戏动作', async () => {
  for (const intent of ['follow', 'goto', 'stop', 'query_status']) {
    const state = fixture(async () => ({ intent, arguments: {}, reply: '已识别，尚未执行' }));
    state.bot.emit('chat', 'Steve', '跟着我');
    assert.equal(state.sent.length, 1);
    await flush();
    assert.deepEqual(state.requests, [['Steve', '跟着我']]);
    assert.equal(state.sent.length, 2);
    assert.deepEqual(state.moves, []);
  }
});

test('所有旧指令优先匹配且不调用 Agent，自身消息不会循环', async () => {
  const state = fixture();
  for (const command of ['你好', '状态', '背包', '附近', '环境', '跟随我', '停止', '前往 1 64 2']) state.bot.emit('chat', 'Steve', command);
  state.bot.emit('chat', 'MineMate', '跟着我');
  await flush();
  assert.equal(state.requests.length, 0);
  assert.deepEqual(state.moves, [['follow', 'Steve'], ['stop', 'Steve'], ['goTo', 'Steve', 1, 64, 2]]);
  assert.equal(state.sent.length, 9);
});

test('待处理云端请求不会阻塞停止指令，断线/死亡/重生后丢弃迟到回复', async () => {
  for (const event of ['end', 'kicked', 'death', 'respawn']) {
    let resolve;
    const state = fixture(() => new Promise((done) => { resolve = done; }));
    state.bot.emit('chat', 'Steve', '聊聊');
    await flush();
    state.bot.emit('chat', 'Steve', '停止');
    assert.equal(state.moves[0][0], 'stop');
    state.bot.emit(event);
    const before = state.sent.length;
    resolve({ reply: '迟到回复' });
    await flush();
    assert.equal(state.sent.length, before);
  }
});

test('Python 意外异常给出固定提示，日志不泄漏异常与常见聊天凭据', async () => {
  const state = fixture(async () => { throw new Error('unit-test-secret'); });
  state.bot.emit('chat', 'Steve', 'LLM_API_KEY=unit-secret sk-unit-secret');
  await flush();
  assert.match(state.sent.at(-1), /暂不可用/);
  assert.doesNotMatch(JSON.stringify({ logs: state.logs, errors: state.errors }), /unit-secret|unit-test-secret/);
});

test('startBot 创建一次 Agent 客户端并在断线或被踢时清理', () => {
  const bot = new EventEmitter();
  bot.chat = () => {};
  let created = 0, disposed = 0;
  startBot({ host: '127.0.0.1', port: 51234, auth: 'offline', username: 'MineMate', agent: { enabled: true } }, {
    createBot: () => bot, createMovement: () => new EventEmitter(), createPerceptionModule: () => ({}),
    logger: { log: () => {}, error: () => {} },
    createAgent: () => { created += 1; return { message: async () => null, dispose: () => { disposed += 1; } }; }
  });
  bot.emit('spawn');
  bot.emit('end');
  assert.equal(created, 1);
  assert.equal(disposed, 1);
});

test('同一事件周期中的停止会阻止旧自然语言请求开始发送', async () => {
  const state = fixture(async () => ({ reply: '不应出现' }));
  state.bot.emit('chat', 'Steve', '帮我执行工具');
  state.bot.emit('chat', 'Steve', '停止');
  await flush();
  assert.equal(state.requests.length, 0);
});
