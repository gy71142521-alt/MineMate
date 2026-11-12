const { distance } = require('../common');
function validEntity(bot, entity, radius) {
  return entity && entity !== bot.entity && entity.id !== bot.entity.id && entity.isValid === true && bot.entities[entity.id] === entity &&
    [entity.position?.x, entity.position?.y, entity.position?.z].every(Number.isFinite) && distance(bot.entity.position, entity.position) <= radius;
}
module.exports = { validEntity };
