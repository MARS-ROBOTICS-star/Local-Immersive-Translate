declare const _globalThis: {
  [key: string]: any;
  Zotero: _ZoteroTypes.Zotero;
  ztoolkit: ZToolkit;
  addon: typeof addon;
};

declare type ZToolkit = ReturnType<
  typeof import("../src/utils/ztoolkit").createZToolkit
>;

declare const ztoolkit: ZToolkit;

declare const rootURI: string;

declare const addon: import("../src/addon").default;

declare const __env__: "production" | "development";
declare const __NEW_GA_MEASUREMENT_ID__: string;
declare const __NEW_GA_API_SECRET__: string;
declare const __OLD_GA_MEASUREMENT_ID__: string;
declare const __OLD_GA_API_SECRET__: string;
