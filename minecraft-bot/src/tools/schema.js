// 有限 JSON Schema 子集；目录定义是唯一来源，两端只实现通用校验器。
const number = (minimum, maximum, integer = false) => ({ type: integer ? 'integer' : 'number', minimum, maximum });
const text = { type: 'string', minLength: 1, maxLength: 64, pattern: '^[a-z][a-z0-9_]*$' };
const object = (properties = {}, required = Object.keys(properties)) => ({ type: 'object', properties, required, additionalProperties: false });
const position = object({ x: number(-29999984, 29999984, true), y: number(-2048, 2047, true), z: number(-29999984, 29999984, true) });
function validate(schema, value) {
  if (schema.enum && !schema.enum.includes(value)) return false;
  if (schema.type === 'object') {
    return value !== null && typeof value === 'object' && !Array.isArray(value) &&
      schema.required.every((key) => Object.hasOwn(value, key)) &&
      Object.keys(value).every((key) => Object.hasOwn(schema.properties, key) && validate(schema.properties[key], value[key]));
  }
  if (schema.type === 'number' || schema.type === 'integer') {
    return Number.isFinite(value) && (schema.type !== 'integer' || Number.isInteger(value)) && value >= schema.minimum && value <= schema.maximum;
  }
  if (schema.type === 'string') return typeof value === 'string' && value.length >= (schema.minLength ?? 0) && value.length <= (schema.maxLength ?? 128) && (!schema.pattern || new RegExp(schema.pattern).test(value));
  return false;
}
module.exports = { number, text, object, position, validate };
