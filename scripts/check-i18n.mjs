// 校验 client.js 的 i18n 完整性：DICT 里 zh/en 键集合一致，且代码里 t('...') 用到的键都存在。
import { readFileSync } from "node:fs";

const file = process.argv[2];
const src = readFileSync(file, "utf8");
const start = src.indexOf("const DICT = {");
const end = src.indexOf("\n    };", start);
const body = src.slice(start + "const DICT = ".length, end + "\n    }".length);
const DICT = eval(`(${body})`);

const zh = Object.keys(DICT.zh);
const en = Object.keys(DICT.en);
const missingEn = zh.filter((k) => !en.includes(k));
const missingZh = en.filter((k) => !zh.includes(k));

const used = new Set([...src.matchAll(/\bt\('([A-Za-z0-9_]+)'\)/g)].map((m) => m[1]));
const undefinedKeys = [...used].filter((k) => !zh.includes(k) || !en.includes(k));
const unused = zh.filter((k) => !used.has(k));

console.log("zh keys:", zh.length, "en keys:", en.length);
console.log("missing in en:", JSON.stringify(missingEn));
console.log("missing in zh:", JSON.stringify(missingZh));
console.log("used but undefined:", JSON.stringify(undefinedKeys));
console.log("defined but unused:", JSON.stringify(unused));
const bad = missingEn.length || missingZh.length || undefinedKeys.length;
console.log(bad ? "I18N FAIL" : "I18N OK");
process.exit(bad ? 1 : 0);
