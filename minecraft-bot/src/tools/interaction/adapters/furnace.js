const { copyItem } = require('../window');
module.exports = {
  type: 'furnace',
  matches: (block) => block.name === 'furnace',
  open: (bot, block) => bot.openFurnace(block),
  inspect: (window) => ({ type: 'furnace', input: copyItem(window.inputItem()), fuel: copyItem(window.fuelItem()), output: copyItem(window.outputItem()),
    fuelRemaining: Number.isFinite(window.fuel) ? window.fuel : null, progress: Number.isFinite(window.progress) ? window.progress : null })
};
