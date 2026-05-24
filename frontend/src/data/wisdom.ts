// Daily wisdom rotation — quotes from documented sources + macro facts with
// citations. Per the dashboard's anti-fabrication principle, every entry has
// a verifiable source. If you can't cite it, don't ship it.
//
// Categories are loose; used later if we want contextual surfacing (e.g.
// surface "patience" entries during loss streaks). For now everything just
// rotates by date hash.

export type WisdomKind = "quote" | "fact";
export type WisdomCategory =
  | "patience"
  | "cut-rule"
  | "regime"
  | "sample-size"
  | "humility"
  | "history";

export interface WisdomEntry {
  kind: WisdomKind;
  text: string;
  attribution: string;
  source: string;
  category: WisdomCategory;
}

export const WISDOM: WisdomEntry[] = [
  // ── Buffett (shareholder letters — verifiable) ─────────────────────────
  {
    kind: "quote",
    text: "Be fearful when others are greedy and greedy when others are fearful.",
    attribution: "Warren Buffett",
    source: "Berkshire Hathaway shareholder letter, 2004",
    category: "regime",
  },
  {
    kind: "quote",
    text: "Our favorite holding period is forever.",
    attribution: "Warren Buffett",
    source: "Berkshire Hathaway shareholder letter, 1988",
    category: "patience",
  },
  {
    kind: "quote",
    text: "Risk comes from not knowing what you're doing.",
    attribution: "Warren Buffett",
    source: "Berkshire Hathaway shareholder letter, 1993",
    category: "humility",
  },
  {
    kind: "quote",
    text: "Price is what you pay; value is what you get.",
    attribution: "Warren Buffett",
    source: "Berkshire Hathaway shareholder letter, 2008",
    category: "patience",
  },
  {
    kind: "quote",
    text: "The stock market is a device for transferring money from the impatient to the patient.",
    attribution: "Warren Buffett",
    source: "Berkshire Hathaway shareholder letter, 1991",
    category: "patience",
  },

  // ── Munger ─────────────────────────────────────────────────────────────
  {
    kind: "quote",
    text: "The big money is not in the buying or the selling, but in the waiting.",
    attribution: "Charlie Munger",
    source: "Poor Charlie's Almanack, 2005",
    category: "patience",
  },
  {
    kind: "quote",
    text: "It is remarkable how much long-term advantage people like us have gotten by trying to be consistently not stupid, instead of trying to be very intelligent.",
    attribution: "Charlie Munger",
    source: "Berkshire Hathaway annual meeting, 1994",
    category: "humility",
  },
  {
    kind: "quote",
    text: "Knowing what you don't know is more useful than being brilliant.",
    attribution: "Charlie Munger",
    source: "Poor Charlie's Almanack, 2005",
    category: "humility",
  },

  // ── Soros / Druckenmiller ──────────────────────────────────────────────
  {
    kind: "quote",
    text: "It's not whether you're right or wrong that's important, but how much money you make when you're right and how much you lose when you're wrong.",
    attribution: "George Soros",
    source: "The Alchemy of Finance, 1987",
    category: "cut-rule",
  },
  {
    kind: "quote",
    text: "I'm only rich because I know when I'm wrong.",
    attribution: "George Soros",
    source: "Soros on Soros, 1995",
    category: "cut-rule",
  },
  {
    kind: "quote",
    text: "If you really see it, you've got to go for the jugular. It takes courage to be a pig.",
    attribution: "Stanley Druckenmiller",
    source: "Lost Tree Club speech, January 18 2015",
    category: "regime",
  },

  // ── Livermore (Reminiscences of a Stock Operator) ──────────────────────
  {
    kind: "quote",
    text: "It never was my thinking that made the big money for me. It always was my sitting.",
    attribution: "Jesse Livermore",
    source: "Reminiscences of a Stock Operator (Lefèvre), 1923, ch. 5",
    category: "patience",
  },
  {
    kind: "quote",
    text: "The market does not beat them. They beat themselves.",
    attribution: "Jesse Livermore",
    source: "Reminiscences of a Stock Operator (Lefèvre), 1923",
    category: "humility",
  },
  {
    kind: "quote",
    text: "There is a time for all things, but I didn't know it. And it is precisely that which beats so many men in Wall Street who are very far from being in the main sucker class.",
    attribution: "Jesse Livermore",
    source: "Reminiscences of a Stock Operator (Lefèvre), 1923, ch. 3",
    category: "regime",
  },

  // ── Lynch / Bogle ──────────────────────────────────────────────────────
  {
    kind: "quote",
    text: "Know what you own, and know why you own it.",
    attribution: "Peter Lynch",
    source: "One Up On Wall Street, 1989",
    category: "humility",
  },
  {
    kind: "quote",
    text: "Far more money has been lost by investors preparing for corrections, or trying to anticipate corrections, than has been lost in corrections themselves.",
    attribution: "Peter Lynch",
    source: "Beating the Street, 1993",
    category: "regime",
  },
  {
    kind: "quote",
    text: "Time is your friend; impulse is your enemy.",
    attribution: "John C. Bogle",
    source: "Common Sense on Mutual Funds, 1999",
    category: "patience",
  },

  // ── Taleb ──────────────────────────────────────────────────────────────
  {
    kind: "quote",
    text: "Don't tell me what you think, tell me what you have in your portfolio.",
    attribution: "Nassim Nicholas Taleb",
    source: "Skin in the Game, 2018",
    category: "humility",
  },
  {
    kind: "quote",
    text: "The three most harmful addictions are heroin, carbohydrates, and a monthly salary.",
    attribution: "Nassim Nicholas Taleb",
    source: "The Bed of Procrustes, 2010",
    category: "humility",
  },

  // ── Klarman / Marks ────────────────────────────────────────────────────
  {
    kind: "quote",
    text: "Risk means more things can happen than will happen.",
    attribution: "Howard Marks (quoting Elroy Dimson)",
    source: "The Most Important Thing, 2011",
    category: "sample-size",
  },
  {
    kind: "quote",
    text: "Being too far ahead of your time is indistinguishable from being wrong.",
    attribution: "Howard Marks",
    source: "Memo: 'Dare to Be Great II', April 2014",
    category: "cut-rule",
  },

  // ── Macro / market-history facts (sourced) ─────────────────────────────
  {
    kind: "fact",
    text: "Since 1928, the S&P 500 has finished positive in roughly 73% of calendar years (≈70 of 96 through 2024).",
    attribution: "S&P 500 historical returns",
    source: "Robert Shiller dataset (NYU Stern) + S&P Dow Jones Indices",
    category: "history",
  },
  {
    kind: "fact",
    text: "The average intra-year drawdown for the S&P 500 from 1980-2023 was -14%, despite finishing positive in 33 of those 44 years.",
    attribution: "Intra-year vs annual returns",
    source: "JPM Asset Management — Guide to the Markets, 2024 edition",
    category: "regime",
  },
  {
    kind: "fact",
    text: "VIX has spent ~80% of trading days below 25 since its inception in January 1990.",
    attribution: "CBOE Volatility Index daily history",
    source: "CBOE / Yahoo Finance ^VIX series, 1990-present",
    category: "regime",
  },
  {
    kind: "fact",
    text: "The longest U.S. bull market on record ran from March 2009 to February 2020 — 131 months.",
    attribution: "S&P 500 bull/bear cycle dating",
    source: "Yardeni Research — S&P 500 bull/bear cycles",
    category: "history",
  },
  {
    kind: "fact",
    text: "The 2000 Nasdaq peak (March 10, 2000) was not reclaimed until April 23, 2015 — over 15 years.",
    attribution: "Nasdaq Composite all-time-high history",
    source: "Nasdaq historical close, ^IXIC",
    category: "history",
  },
  {
    kind: "fact",
    text: "Of the S&P 500's best 10 days each decade, missing them turns a long-term return roughly equal to T-bills. Of the worst 10 days, avoiding them roughly doubles the return. The challenge: they cluster within weeks of each other.",
    attribution: "Concentration of return / drawdown",
    source: "JPM Asset Management — Guide to Retirement, multiple editions",
    category: "patience",
  },
  {
    kind: "fact",
    text: "The 1987 crash (Oct 19, -22.6% in one session) remains the largest single-day percentage drop in S&P 500 history. The market closed positive for the calendar year.",
    attribution: "Black Monday, 1987",
    source: "S&P 500 historical close, NYSE archives",
    category: "history",
  },
  {
    kind: "fact",
    text: "Gold's all-time bull market from August 1999 ($253) to August 2011 ($1,895) returned roughly +650% — outperforming the S&P 500's roughly +5% over the same span.",
    attribution: "Gold vs S&P 500, 1999-2011",
    source: "LBMA Gold Price (London PM fix) + S&P 500 total return",
    category: "history",
  },
  {
    kind: "fact",
    text: "Yield-curve inversions (10Y minus 3M) preceded every U.S. recession since 1955, with one false positive in 1966.",
    attribution: "Yield-curve recession-forecasting record",
    source: "Estrella & Mishkin (NY Fed Working Paper), updated FRED T10Y3M",
    category: "regime",
  },
  {
    kind: "fact",
    text: "In SPY's history (1993-present), the worst single calendar year was 2008 at -36.8%; the best was 1995 at +37.6%.",
    attribution: "SPY annual returns",
    source: "Yahoo Finance SPY, dividend-adjusted close",
    category: "history",
  },
  {
    kind: "fact",
    text: "Median U.S. equity bear market lasts ~9 months and drops ~30%; median bull market lasts ~5 years and gains ~150%. The asymmetry is the entire game.",
    attribution: "Bull vs bear market median durations",
    source: "First Trust Advisors / S&P 500 historical cycles, 1942-2024",
    category: "patience",
  },
];
