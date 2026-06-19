// Strong, opinionated ESLint flat config (ESLint 9+).
// Pairs with Prettier: Prettier owns formatting, ESLint owns correctness — eslint-config-prettier
// turns off any stylistic rules that would fight Prettier. Installed by `northstar project add`.
import js from "@eslint/js";
import globals from "globals";
import prettier from "eslint-config-prettier";

export default [
  js.configs.recommended,
  prettier,
  {
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: { ...globals.node, ...globals.browser },
    },
    rules: {
      "no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "no-undef": "error",
      "no-var": "error",
      "prefer-const": "error",
      "prefer-arrow-callback": "error",
      "prefer-template": "error",
      eqeqeq: ["error", "always"],
      curly: ["error", "all"],
      "no-implicit-coercion": "error",
      "no-throw-literal": "error",
      "no-console": "warn",
    },
  },
  { ignores: ["node_modules/", "dist/", "build/", "coverage/", "*.min.js"] },
];
