# Vendored front-end libraries

Kept in the repository rather than loaded from a CDN, so the app works offline
and a fresh clone draws its graphs with nothing else to fetch.

| File | Library | Version | License |
|---|---|---|---|
| `cytoscape.min.js` | [Cytoscape.js](https://github.com/cytoscape/cytoscape.js) | 3.30.2 | MIT, © The Cytoscape Consortium |
| `dagre.min.js` | [dagre](https://github.com/dagrejs/dagre) | bundled build | MIT, © Chris Pettitt |
| `cytoscape-dagre.js` | [cytoscape-dagre](https://github.com/cytoscape/cytoscape.js-dagre) | bundled build | MIT, © The Cytoscape Consortium |

Each minified file keeps its own license header where the upstream build
includes one. The MIT license permits redistribution with that notice.
