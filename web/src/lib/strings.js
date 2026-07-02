// Centralized game-agnostic UI strings (engine chrome — labels, headers, buttons).
// These are NOT dataset content (that lives in games/<Game>/ and is served by the API);
// they're the engine's own UI copy. English is the base; other languages override keys
// and anything missing falls back to English (deep-merged by uiFor()). Game-specific text
// (place names, narrative, the onboarding manual) is supplied by the server, not here.
const CATALOG = {
  en: {
    appTitle: 'OneLife',
    appTagline: '— prototype slice',
    panels: {
      atmosphere: 'Atmosphere',
      alignment: 'Alignment',
      leaderboard: 'Leaderboard',
      notes: 'Notes',
      log: 'Log',
      logHint: '(your progress)',
    },
    auth: {
      logIn: 'Log in',
      createAccount: 'Create account',
      register: 'Register',
      twofa: 'Set up two-factor auth',
      recovery: 'Save your recovery codes',
    },
    onboarding: {
      title: 'Before you begin',
      quickCheck: 'Quick check',
      begin: 'Begin',
    },
    lobby: {
      chooseGame: 'Choose your game',
      subtitle: 'Each world keeps its own progress — switch any time.',
      continue: 'Continue',
      startOver: 'Start over',
      newGame: 'New game',
      notStarted: 'Not started yet',
      inProgress: 'in progress',
      none: 'No games are available right now.',
    },
    nav: {
      logOut: 'Log out',
      switchGame: 'Switch game',
    },
    howToPlay: 'How to play',
    admin: 'Admin & settings',
  },
  sv: {
    appTagline: '— prototyp',
    panels: {
      atmosphere: 'Atmosfär',
      alignment: 'Sinnelag',
      leaderboard: 'Topplista',
      notes: 'Anteckningar',
      log: 'Logg',
      logHint: '(dina framsteg)',
    },
    auth: {
      logIn: 'Logga in',
      createAccount: 'Skapa konto',
      register: 'Registrera',
      twofa: 'Ställ in tvåfaktorsautentisering',
      recovery: 'Spara dina återställningskoder',
    },
    onboarding: {
      title: 'Innan du börjar',
      quickCheck: 'Snabbkoll',
      begin: 'Börja',
    },
    lobby: {
      chooseGame: 'Välj ditt spel',
      subtitle: 'Varje värld har sina egna framsteg — byt när du vill.',
      continue: 'Fortsätt',
      startOver: 'Börja om',
      newGame: 'Nytt spel',
      notStarted: 'Inte påbörjat än',
      inProgress: 'pågår',
      none: 'Inga spel är tillgängliga just nu.',
    },
    nav: {
      logOut: 'Logga ut',
      switchGame: 'Byt spel',
    },
    howToPlay: 'Så spelar du',
    admin: 'Admin & inställningar',
  },
};

function deepMerge(base, over) {
  const out = Array.isArray(base) ? [...base] : { ...base };
  for (const [k, v] of Object.entries(over || {})) {
    out[k] = (v && typeof v === 'object' && !Array.isArray(v))
      ? deepMerge(base?.[k] || {}, v) : v;
  }
  return out;
}

// The UI object for a language: the target language's strings over the English base, so
// any untranslated key transparently falls back to English.
export function uiFor(lang) {
  return deepMerge(CATALOG.en, CATALOG[lang] || CATALOG[(lang || '').split('-')[0]] || {});
}

// The English base, for any non-reactive usage.
export const UI = CATALOG.en;
