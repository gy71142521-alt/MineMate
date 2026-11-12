const { ensure } = require('../../common');
module.exports = {
  type: 'workbench',
  matches: (block) => block.name === 'crafting_table',
  open: (bot, block) => bot.openBlock(block),
  inspect(window) {
    ensure(window.type?.startsWith('minecraft:crafting'), 'WRONG_WINDOW', '服务器未打开工作台窗口。');
    return { type: 'workbench', windowType: window.type };
  }
};
