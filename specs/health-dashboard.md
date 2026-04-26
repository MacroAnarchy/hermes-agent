# Miru — Product Spec

**Status:** First Draft (compiling, mock data)  
**Date:** 2026-04-26  
**Name:** Miru (Japanese: "to see/look")  
**Repo:** `MacroAnarchy/miru` (PRIVATE)  
**Local:** `~/miru/Miru.swiftpm/`

---

## The Problem

Health tracking data is abundant but inaccessible. Apple Health collects everything, but no app gives you **your body's complete status at a glance**.

Existing apps either:
- Show everything = overwhelming
- Show one thing well = incomplete
- Use tabs/navigation = requires effort
- Invent magic scores nobody asked for ("readiness: 72", "body battery: 46")

Nobody has solved: **one screen, everything, at a glance, zero interpretation.**

## The Product

A health dashboard for iPhone & iPad. Raw data, beautifully displayed.

### Design Philosophy: Data Only, Zero Magic

- ❌ No scores, no coaching, no gamification
- ❌ No "readiness metrics", no "AI insights"
- ❌ No streaks, badges, achievements
- ✅ Raw measurements, cleanly presented
- ✅ Color from user's own baseline, not proprietary algorithms
- ✅ The UI IS the product

---

## App Structure

**2 views only:** NOW + TRENDS. Sport is a card at the bottom of NOW, NOT a separate tab.

```
┌──────────────────────────────────┐
│  NOW (default)                    │
│  ┌────────────────────────────┐  │
│  │  Vitals Card               │  │
│  │  HRV 48h chart + 4 corners │  │
│  └────────────────────────────┘  │
│  ┌────────────────────────────┐  │
│  │  Sleep Card                │  │
│  │  Duration ring + 4 corners │  │
│  └────────────────────────────┘  │
│  ┌────────────────────────────┐  │
│  │  Body Card                 │  │
│  │  Weight ring + 4 corners   │  │
│  └────────────────────────────┘  │
│  ┌────────────────────────────┐  │
│  │  Sport Card                │  │
│  │  Session ring + 4 corners  │  │
│  └────────────────────────────┘  │
│                                   │
│  [toggle] → TRENDS                │
└──────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────┐
│  TRENDS                           │
│  This week vs 4-week average      │
│  HRV / RHR / Sleep / Weight /     │
│  Sessions / Recovery              │
└──────────────────────────────────┘
```

---

## Card Layout (the core UI identity)

Each card = ZStack with center hero + 4 corner metrics pinned to actual corners:

```
┌──────────────────────────────────┐
│            Card Title             │  ← centered
│ ● TL metric        TR metric ●   │  ← .topLeading / .topTrailing
│   value               value      │
│         [ CENTER HERO ]           │  ← chart or ring, fills middle
│                                   │
│ ● BL metric        BR metric ●   │  ← .bottomLeading / .bottomTrailing
│   value               value      │
└──────────────────────────────────┘
```

- Corners pinned via ZStack alignment (.topLeading, .topTrailing, .bottomLeading, .bottomTrailing)
- Each corner: colored dot + muted label on top, bold value below
- Center hero padded horizontally to avoid corner overlap
- Header: centered title only, no arrow

---

## Card Specs

### Vitals Card
| Position | Metric | Display |
|---|---|---|
| **Center** | HRV | 48h hourly overlay chart (today vs yesterday) |
| **TL** | RHR | Latest + 7d delta |
| **TR** | HR | Latest |
| **BL** | VO₂ Max | Latest |
| **BR** | Daylight | Minutes |

### Sleep Card
| Position | Metric | Display |
|---|---|---|
| **Center** | Duration | Ring (hours/8 progress) |
| **TL** | HRV | Latest |
| **TR** | HR | Latest |
| **BL** | SpO₂ | Latest |
| **BR** | Resp Rate | Latest |

Note: Sleep corners show vitals (HRV/HR/SpO₂/Resp), NOT sleep stages.

### Body Card
| Position | Metric | Display |
|---|---|---|
| **Center** | Weight | Ring (latest value) |
| **TL** | Body Fat | Latest + 7d delta |
| **TR** | BMI | Latest |
| **BL** | Energy | Active + Basal today |
| **BR** | Lean Mass | Latest |

### Sport Card
| Position | Metric | Display |
|---|---|---|
| **Center** | Last Session | Ring (duration/60min progress, type label) |
| **TL** | Recovery | Latest session % |
| **TR** | Sessions | 30d count |
| **BL** | Avg Recovery | Mean % |
| **BR** | Training Load | 7d energy sum |

Tap → SportDetailView (drill-down with session list + recovery history)

---

## Color Theme

Solarized dark with muted accent colors (NOT neon system colors):

| Color | RGB | Use |
|---|---|---|
| Green | (0.45, 0.78, 0.50) | Positive trend, good metrics |
| Orange | (0.85, 0.55, 0.30) | Warning, peak values |
| Red | (0.78, 0.35, 0.35) | Decline, alerts |
| Blue/Accent | (0.40, 0.60, 0.82) | Goals, targets, neutral |

Background: black. Card: white 0.10. Primary text: white. Secondary: white 0.60. Tertiary: white 0.35.

---

## Tech Stack

- SwiftUI + Swift Charts
- HealthKit direct (with mock data fallback for Swift Playgrounds)
- Swift Playgrounds `.swiftpm` format (flat files, no subdirectories)
- iOS 26+ deployment target
- 15 Swift files, ~2100 lines

### Files

```
Miru.swiftpm/
├── Package.swift           ← swift-tools-version: 5.9
├── MiruApp.swift           ← @main entry point
├── DashboardView.swift     ← Root view, NOW/TRENDS toggle
├── NowView.swift           ← NOW: 4 stacked cards
├── TrendsView.swift        ← TRENDS: week vs 4-week comparison
├── VitalsCard.swift        ← HRV chart center + 4 corners
├── SleepCard.swift         ← Duration ring + 4 corners
├── BodyCard.swift          ← Weight ring + 4 corners
├── SportCard.swift         ← Session ring + 4 corners
├── RingView.swift          ← Circular progress ring component
├── Theme.swift             ← Colors, formatting, CornerMetric, CardModifier
├── Charts.swift            ← HourlyOverlayChart, Sparkline, SleepStages, RecoveryCurve
├── HealthModels.swift      ← Data structs (VitalsData, SleepData, BodyData, SportData)
├── HealthStoreManager.swift ← HealthKit queries + mock data fallback
└── SportDetailView.swift   ← Drill-down: session detail + recovery history
```

### Dev Environment
- **Build:** iPad Mini (A15, iPadOS 26) via Swift Playgrounds
- **Code:** XPS → Git → iPad pulls from GitHub (MacroAnarchy/miru)
- **MacBook 12 (2019):** Old Xcode, likely can't build iOS 26 target
- **HealthKit:** Unavailable in Swift Playgrounds — mock data only until TestFlight
- **Real data path:** $99/year Apple Developer account → Xcode Cloud → TestFlight → iPhone

---

## Business Model

| Tier | Price | What |
|---|---|---|
| Free | €0 | Full dashboard, your data |
| Premium | €1.99/mo (7-day trial) | Export, custom sources, historical deep dives |

Premium stays on-brand: more data, more access — NOT AI coaching.

---

## Roadmap

### ✅ Done (First Draft)
- [x] 4 cards with ZStack true-corner layout
- [x] Vitals: HRV 48h overlay, RHR/VO₂/HR/Daylight corners
- [x] Sleep: Duration ring, HRV/SpO₂/HR/Resp corners
- [x] Body: Weight ring, BF/Energy/BMI/Lean corners
- [x] Sport: Session ring, Recovery/Sessions/Avg Recovery/Load corners
- [x] Solarized dark theme with muted colors
- [x] 2 views: NOW + TRENDS
- [x] Mock data fallback
- [x] Compiles clean on Swift Playgrounds

### 🔜 Next
- [ ] Real HealthKit data ($99 dev account → TestFlight)
- [ ] Lower iOS target for MacBook Xcode compatibility
- [ ] Tap-to-expand detail views (7d/30d/90d)
- [ ] Error states / empty states
- [ ] App Store submission

### 📋 Future
- [ ] StoreKit subscription (€1.99/mo)
- [ ] Apple Watch complications
- [ ] iOS widgets
- [ ] Health PDF reports / export
- [ ] Custom data sources (Garmin, Oura, Withings)

---

## What This Is NOT

- ❌ Not a fitness tracker
- ❌ Not a coach
- ❌ Not a social app
- ❌ Not an AI coach

## What This IS

- ✅ A dashboard — reads your data, shows your status
- ✅ One screen — everything at once
- ✅ Trend-aware — history baked into every view
- ✅ Opinionated — we decide what matters and how to show it
- ✅ Private — your data, your device, no cloud
- ✅ Honest — raw measurements, no proprietary algorithms

---

## Competitors

- **Apple Health**: data graveyard, unusable
- **Bevel**: nice but limited, still coaches
- **Gentler Streak**: closest — gets "less is more" but still injects coaching
- **Whoop/Oura**: hardware-locked, proprietary scores
- **AutoSleep/HeartWatch**: feature-rich but overwhelming

The gap is **philosophy.** Every app thinks you need help understanding your body. We think you just need to see it clearly.

---

## Success Criteria

> Open the app. In 3 seconds: "HRV is up, sleep is down, weight is flat, training is slipping."

The *story* of your body, not just numbers.
