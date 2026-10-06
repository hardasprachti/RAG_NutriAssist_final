---
name: NutriAI Assistant
colors:
  surface: '#ebfef0'
  surface-dim: '#ccdfd1'
  surface-bright: '#ebfef0'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#e5f8ea'
  surface-container: '#dff3e5'
  surface-container-high: '#daeddf'
  surface-container-highest: '#d4e7d9'
  on-surface: '#0f1f16'
  on-surface-variant: '#414938'
  inverse-surface: '#24342b'
  inverse-on-surface: '#e2f6e7'
  outline: '#717a66'
  outline-variant: '#c1cab3'
  surface-tint: '#376b00'
  primary: '#376b00'
  on-primary: '#ffffff'
  primary-container: '#7cc63a'
  on-primary-container: '#274e00'
  inverse-primary: '#8fda4c'
  secondary: '#785a00'
  on-secondary: '#ffffff'
  secondary-container: '#ffd167'
  on-secondary-container: '#765900'
  tertiary: '#006a65'
  on-tertiary: '#ffffff'
  tertiary-container: '#44c5bc'
  on-tertiary-container: '#004e4a'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#a9f866'
  primary-fixed-dim: '#8fda4c'
  on-primary-fixed: '#0d2000'
  on-primary-fixed-variant: '#285000'
  secondary-fixed: '#ffdf9b'
  secondary-fixed-dim: '#edc157'
  on-secondary-fixed: '#251a00'
  on-secondary-fixed-variant: '#5b4300'
  tertiary-fixed: '#7cf6ec'
  tertiary-fixed-dim: '#5dd9d0'
  on-tertiary-fixed: '#00201e'
  on-tertiary-fixed-variant: '#00504c'
  background: '#ebfef0'
  on-background: '#0f1f16'
  surface-variant: '#d4e7d9'
typography:
  display:
    fontFamily: Nunito Sans
    fontSize: 44px
    fontWeight: '800'
    lineHeight: 52px
  display-mobile:
    fontFamily: Nunito Sans
    fontSize: 34px
    fontWeight: '800'
    lineHeight: 42px
  headline-lg:
    fontFamily: Nunito Sans
    fontSize: 32px
    fontWeight: '800'
    lineHeight: 40px
  headline-lg-mobile:
    fontFamily: Nunito Sans
    fontSize: 26px
    fontWeight: '800'
    lineHeight: 34px
  headline-md:
    fontFamily: Nunito Sans
    fontSize: 22px
    fontWeight: '700'
    lineHeight: 28px
  headline-sm:
    fontFamily: Nunito Sans
    fontSize: 18px
    fontWeight: '700'
    lineHeight: 24px
  body-lg:
    fontFamily: Nunito Sans
    fontSize: 17px
    fontWeight: '400'
    lineHeight: 26px
  body-md:
    fontFamily: Nunito Sans
    fontSize: 15px
    fontWeight: '400'
    lineHeight: 22px
  body-sm:
    fontFamily: Nunito Sans
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  label-lg:
    fontFamily: Nunito Sans
    fontSize: 15px
    fontWeight: '700'
    lineHeight: 20px
  label-md:
    fontFamily: Nunito Sans
    fontSize: 13px
    fontWeight: '700'
    lineHeight: 16px
  label-sm:
    fontFamily: Nunito Sans
    fontSize: 11px
    fontWeight: '800'
    lineHeight: 14px
rounded:
  sm: 0.5rem
  DEFAULT: 1rem
  md: 1.5rem
  lg: 2rem
  xl: 3rem
  full: 9999px
spacing:
  gutter: 1rem
  margin: 1.25rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2rem
---

## Brand & Style

This design system channels the warmth, abundance, and vitality of an artisan farmers market translated into a high-performance modern wellness platform. It deliberately avoids clinical sterility, cold medical charts, and restrictive diet tropes in favor of an uplifting, joyful, and deeply evidence-backed nutrition companion.

The aesthetic philosophy balances organic warmth with contemporary digital precision. Soft cream foundations replace harsh pure whites, creating a gentle canvas where nutrient-rich, farm-fresh accents—citrus lime, ripe mango gold, sun-ripened coral, and brisk market teal—can spark motivation without inducing sensory fatigue. Tactile, pill-shaped interactive nodes and friendly organic radii invite effortless daily logging, AI meal breakdown, and holistic dietary discovery.

## Colors

The palette draws directly from wholesome whole foods, sunlight, and botanical vitality:

- **Primary (`#7CC63A`)**: Crisp market lime. Used for core positive feedback, primary calls to action, logging affirmations, and optimal macro-nutrient thresholds.
- **Secondary (`#FFD166`)**: Golden harvest yellow. Represents energy, micronutrient highlights, metabolic vitality, and streak achievements.
- **Tertiary (`#4ECDC4`)**: Fresh market teal. Denotes hydration tracking, dietary fiber indicators, AI conversational context, and calm wellness balance.
- **Accent Coral (`#FF6B6B`)**: Sun-ripened coral orange. Reserved for protein metrics, immediate meal insights, active timer alerts, and dynamic badges.
- **Canvas Cream (`#FDFDF7`)**: The warm off-white foundational background providing soft daylight illumination across all viewports.
- **Neutral Foreground (`#1D2D24`)**: Deep forest slate. Replaces sterile grays and pure blacks with a rich chlorophyll-tinted tone for ultra-crisp typographic hierarchy and legibility.

### Surface Tonal Levels
- **Base Canvas**: `#FDFDF7` (Warm Cream)
- **Surface Level 1 (Cards & Modules)**: `#FFFFFF`
- **Surface Level 2 (Inset Wells & Sub-panels)**: `#F4F4EB`
- **Surface Muted / Outlines**: `#E8E8DC`

## Typography

The type structure relies on **Nunito Sans** for its balanced humanist proportions and gently softened terminals. It delivers natural friendliness without sacrificing data density or analytical authority when presenting scientific nutritional findings.

Display and headline levels utilize heavier weights (700 and 800) to create spirited focal points and readable meal summaries. Body copy maintains generous line heights (1.45 to 1.55x) for fatigue-free reading of ingredient compositions and metabolic recommendations. Labels and data tokens leverage assertive bold weights with slightly expanded optical tracking to ensure legibility on ambient-lit mobile screens.

## Layout & Spacing

The layout model adheres to a comfortable, fluid multi-column grid system tuned for dense health data and airy conversational interfaces:

- **Mobile (<768px)**: 4-column layout, `1.25rem` (20px) outer canvas margin, and `1rem` (16px) gutters. Bottom navigation is pinned with a safe-area lift, ensuring one-hand thumbnail logging.
- **Tablet (768px - 1024px)**: 8-column layout, `2rem` (32px) margins, enabling side-by-side food diary lists and interactive macro distribution wheels.
- **Desktop (>1024px)**: 12-column layout capped at a maximum width of `1200px`, `2.5rem` (40px) outer margins, dividing conversational AI assistance, meal timeline, and analytical charts into structured parallel panes.

Spacing rhythms consistently scale in units of 4px and 8px, prioritizing breathable card containers to keep complex macronutrient graphs clean and approachable.

## Elevation & Depth

Visual hierarchy abandons harsh industrial drop shadows in favor of **sunlit ambient depth**. Surfaces cast warm, diffused botanical shadows that mimic natural daylight over a clean kitchen table:

- **Flat/Subsurface**: Tinted containers (`#F4F4EB`) with no shadow, embedded within cards for micro-metrics.
- **Elevated Card**: `0 4px 20px -2px rgba(29, 45, 36, 0.05), 0 2px 6px -1px rgba(212, 175, 55, 0.06)`. Delivers a gentle lift above the `#FDFDF7` cream canvas.
- **Floating Controls & Active Popovers**: `0 12px 32px -4px rgba(29, 45, 36, 0.08), 0 4px 12px -2px rgba(124, 198, 58, 0.12)`.
- **Modals & Bottom Drawers**: `0 24px 48px -8px rgba(29, 45, 36, 0.14)`.

## Shapes

The shape system employs an organic geometry with high tactile affordance:

- **Pill Elements (`999px`)**: Standard for all primary, secondary, and tertiary action buttons, dynamic filter pills, and quick-tag macros.
- **Content Cards (`16px` / `1rem`)**: Rounded rectangular cards framing daily summaries, ingredient logs, and dietary breakdowns.
- **Chat & Insight Bubbles (`18px` with asymmetric `4px` corner)**: Conversational AI speech nodes feature smooth 18px radii on three corners, anchored by a tight 4px radius pointing toward the speaker origin.
- **Input Fields (`28px`)**: Pill-like search and natural-language prompt bars that suggest tactile softness.

## Components

### Buttons
- **Primary Action**: Background `#7CC63A`, text `#FFFFFF`, full pill radius (`999px`), padding `12px 24px`. Subtle active state scale `0.98` with enhanced ambient lime glow.
- **Secondary / Wellness Action**: Background `#FFD166`, text `#1D2D24`, pill radius (`999px`), padding `12px 24px`.
- **Ghost / Tertiary Action**: Border `1.5px solid #E8E8DC`, background transparent, text `#1D2D24`, pill radius (`999px`).

### Chips & Filter Tags
- Height of 32px or 36px, full pill shape (`999px`).
- Inactive state: `#F4F4EB` background with `#1D2D24` text.
- Active nutrient state: Dynamic tinting corresponding to the category (e.g., `#7CC63A` for Greens, `#FF6B6B` for Protein, `#4ECDC4` for Hydration), paired with bold contrast typography.

### Input Fields & Search Bars
- Standard height 56px, `28px` border radius, `#FFFFFF` fill with an understated `1.5px solid #E8E8DC` border.
- Focus state: Border transitions to `#7CC63A` accompanied by an ambient warm halo: `0 0 0 4px rgba(124, 198, 58, 0.15)`.

### Cards & Meal Containers
- Pure white (`#FFFFFF`) surface, `16px` border radius, elevated by the warm ambient shadow.
- Interior padding set to `1.25rem` (20px). Subsections and nutrient breakdowns sit within `#F4F4EB` rounded nests (`12px`).

### AI Conversational Bubbles
- **NutriAI Assistant Bubble**: Background `#FFFFFF`, border `1px solid #E8E8DC`, corner radii `18px 18px 18px 4px`, padding `14px 18px`. Accentuated with a small teal (`#4ECDC4`) spark badge.
- **User Entry Bubble**: Background `#7CC63A`, text `#FFFFFF`, corner radii `18px 18px 4px 18px`, padding `14px 18px`.

### Selection Controls (Checkboxes & Radios)
- **Checkboxes**: `20px x 20px`, `6px` radius. When selected, solid `#7CC63A` with crisp white check mark icon.
- **Radio Buttons**: `20px x 20px`, fully circular (`999px`), with an inner `#7CC63A` circle indicator surrounded by a 3px white safety ring.

### Nutrient Progress Rings & Gauges
- Thick circular arcs (`8px - 12px` stroke) set against a soft `#F4F4EB` track.
- Filled with joyful multi-segment caps using Coral (`#FF6B6B`), Golden Yellow (`#FFD166`), and Teal (`#4ECDC4`).