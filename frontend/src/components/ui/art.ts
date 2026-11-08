// D40: the department avatars and empty-state art, copied from docs/designs/inside/illustrations
// (original line art; C2PA metadata stripped). Static strings, never data from the log. Generated.

export interface Art { vb: string; svg: string }

export const AVATAR_ART: Record<string, Art> = {
  "acct": {
    "vb": "0 0 48 48",
    "svg": "<rect x=\".5\" y=\".5\" width=\"47\" height=\"47\" rx=\"14\" fill=\"#C77DFF\" fill-opacity=\".18\" stroke=\"#C77DFF\" stroke-opacity=\".45\"/><g transform=\"translate(12 12)\" fill=\"none\" stroke=\"#C77DFF\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M5 5.2A1.7 1.7 0 016.7 3.5H18v14.3H6.7A1.7 1.7 0 005 19.5z\"/><path d=\"M5 19.5a1.7 1.7 0 001.7 1.7H18v-3.4\"/><path d=\"M8.6 8h6.2M8.6 11.2h6.2M8.6 14.4h3.6\"/></g>"
  },
  "ceo": {
    "vb": "0 0 48 48",
    "svg": "<rect x=\".5\" y=\".5\" width=\"47\" height=\"47\" rx=\"14\" fill=\"#F2F3EE\" fill-opacity=\".18\" stroke=\"#F2F3EE\" stroke-opacity=\".45\"/><g transform=\"translate(12 12)\" fill=\"none\" stroke=\"#F2F3EE\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><rect x=\"9.2\" y=\"3.5\" width=\"5.6\" height=\"10\" rx=\"2.8\"/><path d=\"M6.5 11.2a5.5 5.5 0 0011 0M12 16.7v3.8M9 20.5h6\"/></g>"
  },
  "cos": {
    "vb": "0 0 48 48",
    "svg": "<rect x=\".5\" y=\".5\" width=\"47\" height=\"47\" rx=\"14\" fill=\"#FF4D3D\" fill-opacity=\".18\" stroke=\"#FF4D3D\" stroke-opacity=\".45\"/><g transform=\"translate(12 12)\" fill=\"none\" stroke=\"#FF4D3D\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M12 3.5l2.1 5.4 5.4 2.1-5.4 2.1L12 18.5l-2.1-5.4L4.5 11l5.4-2.1z\"/><circle cx=\"18.5\" cy=\"18.5\" r=\"1.4\"/></g>"
  },
  "hr": {
    "vb": "0 0 48 48",
    "svg": "<rect x=\".5\" y=\".5\" width=\"47\" height=\"47\" rx=\"14\" fill=\"#2EE6D6\" fill-opacity=\".18\" stroke=\"#2EE6D6\" stroke-opacity=\".45\"/><g transform=\"translate(12 12)\" fill=\"none\" stroke=\"#2EE6D6\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><circle cx=\"9\" cy=\"8.6\" r=\"3\"/><path d=\"M3.8 19.2a5.2 5.2 0 0110.4 0\"/><circle cx=\"16.6\" cy=\"9.6\" r=\"2.4\"/><path d=\"M15.4 14.2a4.3 4.3 0 014.8 4.4\"/></g>"
  },
  "intake": {
    "vb": "0 0 48 48",
    "svg": "<rect x=\".5\" y=\".5\" width=\"47\" height=\"47\" rx=\"14\" fill=\"#4DA3FF\" fill-opacity=\".18\" stroke=\"#4DA3FF\" stroke-opacity=\".45\"/><g transform=\"translate(12 12)\" fill=\"none\" stroke=\"#4DA3FF\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M4 13.5l2.2-7.1A1.6 1.6 0 017.7 5.3h8.6a1.6 1.6 0 011.5 1.1L20 13.5v4.2a1.5 1.5 0 01-1.5 1.5h-13A1.5 1.5 0 014 17.7z\"/><path d=\"M4 13.5h4.4l1.3 2.3h4.6l1.3-2.3H20\"/><path d=\"M12 8v4.2M10.2 10.5L12 12.3l1.8-1.8\"/></g>"
  },
  "mkt": {
    "vb": "0 0 48 48",
    "svg": "<rect x=\".5\" y=\".5\" width=\"47\" height=\"47\" rx=\"14\" fill=\"#FF6FB5\" fill-opacity=\".18\" stroke=\"#FF6FB5\" stroke-opacity=\".45\"/><g transform=\"translate(12 12)\" fill=\"none\" stroke=\"#FF6FB5\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M4.5 10.3v3.4a1 1 0 001 1h2.3l6.7 4V5.3l-6.7 4H5.5a1 1 0 00-1 1z\"/><path d=\"M8.2 14.7l1 4.6\"/><path d=\"M17.6 9.2a4 4 0 010 5.6M19.8 7a7 7 0 010 10\"/></g>"
  },
  "qa": {
    "vb": "0 0 48 48",
    "svg": "<rect x=\".5\" y=\".5\" width=\"47\" height=\"47\" rx=\"14\" fill=\"#3DDC84\" fill-opacity=\".18\" stroke=\"#3DDC84\" stroke-opacity=\".45\"/><g transform=\"translate(12 12)\" fill=\"none\" stroke=\"#3DDC84\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><circle cx=\"10.5\" cy=\"10.5\" r=\"6\"/><path d=\"M15 15l4.8 4.8\"/><path d=\"M7.8 10.6l1.9 1.9 3.3-3.6\"/></g>"
  },
  "tax": {
    "vb": "0 0 48 48",
    "svg": "<rect x=\".5\" y=\".5\" width=\"47\" height=\"47\" rx=\"14\" fill=\"#FFB547\" fill-opacity=\".18\" stroke=\"#FFB547\" stroke-opacity=\".45\"/><g transform=\"translate(12 12)\" fill=\"none\" stroke=\"#FFB547\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><rect x=\"5.5\" y=\"3.5\" width=\"13\" height=\"17\" rx=\"2.2\"/><rect x=\"8\" y=\"6.2\" width=\"8\" height=\"3.3\" rx=\".8\"/><path d=\"M8.6 13h.01M12 13h.01M15.4 13h.01M8.6 16.6h.01M12 16.6h.01M15.4 16.6h.01\"/></g>"
  }
};

export const EMPTY_ART = {
  "all-clear": {
    "vb": "0 0 160 100",
    "svg": "<circle cx=\"80\" cy=\"50\" r=\"30\" fill=\"rgba(242,243,238,.12)\" stroke=\"#BEF243\" stroke-width=\"2\"/><path d=\"M66 50l10 10 20-22\" fill=\"none\" stroke=\"#BEF243\" stroke-width=\"4\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><circle cx=\"36\" cy=\"26\" r=\"3\" fill=\"rgba(242,243,238,.28)\"/><circle cx=\"128\" cy=\"78\" r=\"3\" fill=\"rgba(242,243,238,.28)\"/><path d=\"M126 22l4 4m0-4l-4 4\" stroke=\"#BEF243\" stroke-width=\"2\" stroke-linecap=\"round\"/>"
  },
  "calendar": {
    "vb": "0 0 160 100",
    "svg": "<rect x=\"40\" y=\"18\" width=\"80\" height=\"68\" rx=\"10\" fill=\"rgba(242,243,238,.12)\" stroke=\"rgba(242,243,238,.28)\"/><path d=\"M40 36h80M58 12v12M102 12v12\" stroke=\"rgba(242,243,238,.28)\" stroke-width=\"2\" stroke-linecap=\"round\"/><rect x=\"52\" y=\"46\" width=\"14\" height=\"10\" rx=\"3\" fill=\"rgba(242,243,238,.28)\"/><rect x=\"72\" y=\"46\" width=\"14\" height=\"10\" rx=\"3\" fill=\"#BEF243\"/><rect x=\"92\" y=\"46\" width=\"14\" height=\"10\" rx=\"3\" fill=\"rgba(242,243,238,.28)\"/><rect x=\"52\" y=\"62\" width=\"14\" height=\"10\" rx=\"3\" fill=\"rgba(242,243,238,.28)\"/><rect x=\"72\" y=\"62\" width=\"14\" height=\"10\" rx=\"3\" fill=\"rgba(242,243,238,.28)\"/>"
  },
  "inbox": {
    "vb": "0 0 160 100",
    "svg": "<rect x=\"38\" y=\"30\" width=\"84\" height=\"54\" rx=\"10\" fill=\"rgba(242,243,238,.12)\" stroke=\"rgba(242,243,238,.28)\"/><path d=\"M38 62h22l6 9h28l6-9h22\" fill=\"none\" stroke=\"rgba(242,243,238,.28)\" stroke-width=\"2\"/><rect x=\"58\" y=\"14\" width=\"44\" height=\"30\" rx=\"5\" fill=\"#0F110D\" stroke=\"#BEF243\" stroke-width=\"2\"/><path d=\"M58 18l22 15 22-15\" fill=\"none\" stroke=\"#BEF243\" stroke-width=\"2\"/><circle cx=\"122\" cy=\"20\" r=\"3\" fill=\"#BEF243\"/><circle cx=\"34\" cy=\"24\" r=\"2\" fill=\"rgba(242,243,238,.28)\"/>"
  },
  "phone": {
    "vb": "0 0 160 100",
    "svg": "<rect x=\"62\" y=\"10\" width=\"40\" height=\"80\" rx=\"9\" fill=\"rgba(242,243,238,.12)\" stroke=\"rgba(242,243,238,.28)\" stroke-width=\"2\"/><path d=\"M74 50h4l3-8 4 16 3-12 2 4h4\" fill=\"none\" stroke=\"#BEF243\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><rect x=\"76\" y=\"78\" width=\"12\" height=\"3\" rx=\"1.5\" fill=\"rgba(242,243,238,.28)\"/><path d=\"M42 40a22 22 0 000 22M32 34a32 32 0 000 34M122 40a22 22 0 010 22M132 34a32 32 0 010 34\" fill=\"none\" stroke=\"rgba(242,243,238,.28)\" stroke-width=\"2\" stroke-linecap=\"round\"/>"
  },
  "search": {
    "vb": "0 0 160 100",
    "svg": "<circle cx=\"74\" cy=\"46\" r=\"24\" fill=\"rgba(242,243,238,.12)\" stroke=\"rgba(242,243,238,.28)\" stroke-width=\"2\"/><path d=\"M92 64l18 18\" stroke=\"#BEF243\" stroke-width=\"4\" stroke-linecap=\"round\"/><path d=\"M62 40h24M62 48h16\" stroke=\"rgba(242,243,238,.28)\" stroke-width=\"3\" stroke-linecap=\"round\"/><text x=\"66\" y=\"86\" font-family=\"Geist Mono, monospace\" font-size=\"9\" fill=\"rgba(242,243,238,.28)\">\u00a7 503</text>"
  },
  "sheet": {
    "vb": "0 0 160 100",
    "svg": "<rect x=\"34\" y=\"16\" width=\"92\" height=\"70\" rx=\"9\" fill=\"rgba(242,243,238,.12)\" stroke=\"rgba(242,243,238,.28)\"/><path d=\"M34 34h92M34 50h92M34 66h92M70 16v70\" stroke=\"rgba(242,243,238,.28)\"/><rect x=\"74\" y=\"54\" width=\"48\" height=\"8\" rx=\"3\" fill=\"#BEF243\"/><path d=\"M120 26l6-6m0 0l3 3m-3-3l-8 8\" stroke=\"#BEF243\" stroke-width=\"2\" stroke-linecap=\"round\"/>"
  },
  "social": {
    "vb": "0 0 160 100",
    "svg": "<rect x=\"30\" y=\"18\" width=\"70\" height=\"64\" rx=\"10\" fill=\"rgba(242,243,238,.12)\" stroke=\"rgba(242,243,238,.28)\"/><rect x=\"38\" y=\"26\" width=\"54\" height=\"28\" rx=\"5\" fill=\"#0F110D\" stroke=\"#BEF243\" stroke-width=\"1.6\"/><path d=\"M42 50l12-12 9 9 7-6 18 9\" fill=\"none\" stroke=\"#BEF243\" stroke-width=\"1.6\"/><path d=\"M38 62h40M38 70h26\" stroke=\"rgba(242,243,238,.28)\" stroke-width=\"3\" stroke-linecap=\"round\"/><path d=\"M112 34c8 0 14 6 14 14s-6 14-14 14\" fill=\"none\" stroke=\"rgba(242,243,238,.28)\" stroke-width=\"2\"/><path d=\"M108 40c4 0 8 4 8 8s-4 8-8 8\" fill=\"none\" stroke=\"#BEF243\" stroke-width=\"2\"/>"
  }
} as const satisfies Record<string, Art>;
