import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  build: {
    target: "es2022",
    // The district bundle is fetched at runtime, not inlined.
    assetsInlineLimit: 0,
  },
});
