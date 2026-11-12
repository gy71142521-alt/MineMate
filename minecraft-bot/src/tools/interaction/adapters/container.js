const { copyItem } = require('../window');
module.exports = {
  type: 'container',
  matches: (block) => ['chest', 'trapped_chest', 'barrel'].includes(block.name),
  open: (bot, block) => bot.openContainer(block),
  inspect: (window) => ({ type: 'container', items: window.slots.slice(0, window.inventoryStart).map(copyItem).filter(Boolean) })
};
