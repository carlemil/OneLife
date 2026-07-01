// Centralized game-agnostic UI strings (engine chrome — labels, headers, buttons).
// These are NOT dataset content (that lives in games/<Game>/ and is served by the
// API); they're the engine's own UI copy, gathered here so it's in one place and a
// future translation/theming pass has a single source. Game-specific text (place
// names, narrative, the onboarding manual) is supplied by the server, not here.
export const UI = {
  // Branding (the engine/product name, not the dataset).
  appTitle: 'OneLife',
  appTagline: '— prototype slice',

  // Side-panel section headers.
  panels: {
    atmosphere: 'Atmosphere',
    alignment: 'Alignment',
    leaderboard: 'Leaderboard',
    notes: 'Notes',
    log: 'Log',
    logHint: '(your progress)',
  },

  // Auth / onboarding / lobby screen headers.
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
  },
  lobby: { chooseGame: 'Choose your game' },

  // Modals / nav.
  howToPlay: 'How to play',
  admin: 'Admin & settings',
};
