import { Language } from "./types";
import { langMap, nativeLangMap, zhCNLangMap, zhTWLangMap } from "./config";

export const getLanguages: () => Language[] = () => {
  return Object.keys(langMap) as Language[];
};

export const getLanguageOptions: (interfaceLanguage: Language) => {
  value: string;
  label: string;
}[] = (interfaceLanguage) => {
  return getLanguages().map((lang) => {
    return {
      value: lang,
      label: getLanguageName(lang, interfaceLanguage),
    };
  });
};

export function getLanguageName(lang: Language, interfaceLanguage: Language) {
  const nativeLang = nativeLangMap[lang] || lang;
  const fallbackLang = langMap[lang] || lang;
  const zhLang = zhCNLangMap[lang];
  const zhTWLang = zhTWLangMap[lang];
  const internalNames: Record<string, string> = {
    "zh-CN": zhLang,
    "zh-TW": zhTWLang,
    en: fallbackLang,
  };

  if (lang === interfaceLanguage) {
    return nativeLang;
  }

  const locale = internalNames[interfaceLanguage] ?? fallbackLang;

  return `${locale} (${nativeLang})`;
}

export { langMap, nativeLangMap, zhCNLangMap, zhTWLangMap } from "./config";
