const { ensure } = require('../common');
async function withWindow(ctx, open, use) {
  const { bot } = ctx;
  ensure(!bot.currentWindow, 'WINDOW_BUSY', '已有窗口打开，请先结束原交互。');
  let window;
  const closed = new Set();
  const close = () => {
    const current = window ?? bot.currentWindow;
    if (!current || closed.has(current)) return;
    closed.add(current);
    return bot.closeWindow(current);
  };
  ctx.cleanup(close);
  try {
    // 不用 step 丢弃返回值：取消后迟到的窗口仍须被 finally 关闭。
    ctx.check(); window = await open(); ctx.check();
    return await use(window);
  } finally { await close(); }
}
const copyItem = (item) => item ? { name: item.name, count: item.count, slot: item.slot ?? null } : null;
module.exports = { withWindow, copyItem };
