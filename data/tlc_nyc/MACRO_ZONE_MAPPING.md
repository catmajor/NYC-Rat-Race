# Rat Race TLC macro-zone mapping

`taxi_zone_lookup.csv` is the official TLC lookup table. The generated
`taxi_zone_game_map.csv` translates its numeric `LocationID` values into the
nine Rat Race zones using `data-aquisition/build_zone_map.py`.

The mapping is deliberately explicit for the MVP:

- EWR/JFK/LaGuardia are `airports`.
- Bronx neighborhoods are `harlem`.
- Queens neighborhoods are `queens_west` because the MVP has no separate
  Queens East zone.
- Staten Island neighborhoods are grouped with `south_brooklyn` as the
  southern outer-borough macro-zone.
- Brooklyn neighborhoods are split into named northern neighborhoods and
  `south_brooklyn`.
- Manhattan neighborhoods are assigned by named families: Harlem, Upper West,
  Upper East, Downtown, with remaining central neighborhoods in `midtown`.
- TLC's `Unknown`, `N/A`, and `Outside of NYC` rows are excluded.

This is a gameplay aggregation, not an official geographic boundary. Keep the
source columns in the generated CSV so the assignment remains auditable.
