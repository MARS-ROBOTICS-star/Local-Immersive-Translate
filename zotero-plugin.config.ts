import { defineConfig } from "zotero-plugin-scaffold";
import { readFileSync } from "node:fs";
import pkg from "./package.json";

const toolkitImportHelperBefore =
  'if (typeof ChromeUtils.import === "undefined") return ChromeUtils.importESModule(path, { global: "contextual" });\n\tif (path.endsWith(".sys.mjs")) path = path.replace(/\\.sys\\.mjs$/, ".jsm");\n\treturn ChromeUtils.import(path);';

const toolkitImportHelperAfter =
  'try {\n\t\treturn ChromeUtils.importESModule(path, { global: "contextual" });\n\t} catch (error) {\n\t\tif (typeof ChromeUtils["import"] === "undefined") throw error;\n\t}\n\tif (path.endsWith(".sys.mjs")) path = path.replace(/\\.sys\\.mjs$/, ".jsm");\n\treturn ChromeUtils["import"](path);';

const toolkitImportCompatPlugin = {
  name: "toolkit-import-compat",
  setup(build: any) {
    build.onLoad(
      { filter: /zotero-plugin-toolkit\/dist\/index\.js$/ },
      (args: { path: string }) => {
        const source = readFileSync(args.path, "utf8");
        if (!source.includes(toolkitImportHelperBefore)) {
          throw new Error(
            "zotero-plugin-toolkit import helper shape changed; update toolkit-import-compat patch before building.",
          );
        }
        return {
          contents: source.replace(
            toolkitImportHelperBefore,
            toolkitImportHelperAfter,
          ),
          loader: "js",
        };
      },
    );
  },
};

export default defineConfig({
  source: ["src", "addon"],
  dist: "dist",
  name: pkg.config.addonName,
  id: pkg.config.addonID,
  namespace: pkg.config.addonRef,
  updateURL: `https://github.com/{{owner}}/{{repo}}/releases/download/release/${
    pkg.version.includes("-") ? "update-beta.json" : "update.json"
  }`,
  xpiName: pkg.config.xpiName,
  xpiDownloadLink:
    "https://github.com/{{owner}}/{{repo}}/releases/download/v{{version}}/{{xpiName}}.xpi",

  build: {
    assets: ["addon/**/*.*"],
    define: {
      ...pkg.config,
      author: pkg.author,
      description: pkg.description,
      homepage: pkg.homepage,
      buildVersion: pkg.version,
      buildTime: "{{buildTime}}",
    },
    prefs: {
      prefix: pkg.config.prefsPrefix,
    },
    esbuildOptions: [
      {
        entryPoints: ["src/index.ts"],
        define: {
          __env__: `"${process.env.NODE_ENV}"`,
          __NEW_GA_MEASUREMENT_ID__: `"${process.env.NEW_GA_MEASUREMENT_ID}"`,
          __NEW_GA_API_SECRET__: `"${process.env.NEW_GA_API_SECRET}"`,
          __OLD_GA_MEASUREMENT_ID__: `"${process.env.OLD_GA_MEASUREMENT_ID}"`,
          __OLD_GA_API_SECRET__: `"${process.env.OLD_GA_API_SECRET}"`,
        },
        bundle: true,
        plugins: [toolkitImportCompatPlugin],
        target: "firefox115",
        outfile: `dist/addon/content/scripts/${pkg.config.addonRef}.js`,
      },
    ],
  },

  // If you need to see a more detailed log, uncomment the following line:
  // logLevel: "trace",
});
