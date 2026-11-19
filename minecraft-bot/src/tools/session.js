const { randomUUID } = require('node:crypto');
const { ensure } = require('./common');

// 单进程租约：覆盖整个后台任务；失去心跳后撤销，重启使用新实例 ID。
function createSession({ access, isReady, isBusy, onRevoke, ttlMs = 30000, now = Date.now }) {
  const instanceId = randomUUID();
  let lease = null, timer;
  function revoke(notify = true) {
    const previous = lease;
    lease = null;
    clearTimeout(timer);
    if (previous && notify) onRevoke(previous);
  }
  function expire() { if (lease && now() >= lease.expiresAt) revoke(); }
  function arm() {
    clearTimeout(timer);
    timer = setTimeout(expire, Math.max(1, lease.expiresAt - now()));
    timer.unref();
  }
  function authorize(context = {}, username, mutates = true, priority = false) {
    expire();
    ensure(!context.instanceId || context.instanceId === instanceId, 'STALE_INSTANCE', 'Node 实例已变化，禁止续跑旧任务。');
    if (context.leaseId) ensure(lease && context.leaseId === lease.leaseId && username.toLowerCase() === lease.username.toLowerCase(), 'STALE_LEASE', '任务控制租约已失效。');
    if (mutates && lease && !priority) ensure(context.leaseId === lease.leaseId, 'BUSY', '后台任务持有控制权，请先停止当前任务。');
  }
  function update(action, username, context) {
    expire();
    ensure(access(username), 'FORBIDDEN', '无权管理游戏控制租约。');
    ensure(context.instanceId === instanceId, 'STALE_INSTANCE', 'Node 实例已变化。');
    if (action === 'acquire') {
      ensure(!lease && !isBusy(), 'BUSY', '已有操作或移动任务正在执行，请先停止。');
      ensure(isReady(), 'BOT_NOT_READY', 'Bot 尚未就绪。');
      lease = { leaseId: randomUUID(), username, expiresAt: now() + ttlMs };
      arm();
    } else {
      ensure(lease && context.leaseId === lease.leaseId && username.toLowerCase() === lease.username.toLowerCase(), 'STALE_LEASE', '任务控制租约已失效。');
      if (action === 'release') { revoke(); return { instanceId, lease: null }; }
      ensure(action === 'heartbeat', 'INVALID_ARGUMENT', '租约动作无效。');
      lease.expiresAt = now() + ttlMs;
      arm();
    }
    return { instanceId, lease: { ...lease } };
  }
  return { instanceId, authorize, update, revoke, inspect: () => { expire(); return lease ? { ...lease } : null; } };
}
module.exports = { createSession };
