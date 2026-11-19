# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Workforce/planning analysts configure and review staffing estimates; managers review the resulting headcount and capacity summary.

## Product Purpose

Estimate staffing capacity from an uploaded hourly-demand workbook. Success means users can understand the effective headcount, simultaneous peak, and hourly coverage well enough to plan or review staffing.

## Positioning

The model presents staffing as base, relief, and effective headcount, while keeping contracted headcount separate from active hourly capacity. No broader market-positioning claim is established.

## Operating Context

Users upload an `.xlsx` demand file, set service and shrinkage assumptions, review weekly capacity and planned breaks, and can export the results to Excel.

## Capabilities and Constraints

- Preserve existing Erlang C and shrinkage calculations, approved 12x36 and 5x2 schedules, hourly coverage, break planning, and XLSX export.
- Make the result summary easy to find, especially effective HC and simultaneous peak.
- Keep HC separate from active capacity and identify 12x36 quantities as posts and 5x2 quantities as people.
- Multi-skill remains out of scope.

## Evidence on Hand

- Streamlit app: `app_dimensionamento5.py`.
- Staffing model and tests: `staffing_model.py`, `tests/`.
- Example demand workbooks: `base_calculo/`.
- No approved logo or other brand assets identified.

## Product Principles

- Make staffing assumptions and resulting capacity legible.
- Keep contracted HC distinct from simultaneous active capacity.
- Support both detailed planning and quick management review.
- Preserve traceability through the hourly breakdown and Excel export.

## Accessibility & Inclusion

No product-specific accessibility standard has been established.
