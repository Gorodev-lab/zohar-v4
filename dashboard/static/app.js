// ============================================================================
// dashboard/static/app.js
// Asignación de colores vivos por comunidad/clúster para el grafo de Zohar v4
// ============================================================================

// Paleta de 12 colores hexadecimales contrastantes y accesibles
const COMMUNITY_COLORS = [
    '#FF6B6B', // Rojo coral
    '#4ECDC4', // Turquesa
    '#45B7D1', // Azul cielo
    '#FFA07A', // Salmón claro
    '#98D8C8', // Verde menta
    '#F7DC6F', // Amarillo mostaza
    '#BB8FCE', // Lavanda
    '#85C1E9', // Azul pastel
    '#F8C471', // Naranja suave
    '#82E0AA', // Verde lima
    '#F1948A', // Rosa suave
    '#A5D8FF'  // Azul cielo claro
];

// Función para asignar color a un nodo según su comunidad
function getNodeColor(node) {
    const community = node.community || node.cluster_id || 0;
    const colorIndex = Math.abs(community) % COMMUNITY_COLORS.length;
    return COMMUNITY_COLORS[colorIndex];
}

// Configuración de Cytoscape.js para el grafo de Zohar v4
const cy = cytoscape({
    container: document.getElementById('cy'),
    
    style: [
        // Estilo base para los nodos
        {
            selector: 'node',
            style: {
                'background-color': function(ele) {
                    return getNodeColor(ele.data());
                },
                'border-width': 2,
                'border-color': '#FFFFFF',
                'width': 25,
                'height': 25,
                'label': 'data(id)',
                'font-size': 10,
                'text-valign': 'center',
                'text-halign': 'center',
                'color': '#000000',
                'text-outline-width': 1,
                'text-outline-color': '#FFFFFF'
            }
        },
        
        // Estilo para los enlaces
        {
            selector: 'edge',
            style: {
                'width': 1.5,
                'line-color': '#A0A0A0',
                'curve-style': 'bezier',
                'opacity': 0.7,
                'target-arrow-shape': 'triangle',
                'target-arrow-color': '#A0A0A0'
            }
        },
        
        // Estilo para nodos seleccionados
        {
            selector: 'node:selected',
            style: {
                'border-width': 3,
                'border-color': '#000000',
                'background-color': function(ele) {
                    const baseColor = getNodeColor(ele.data());
                    // Oscurecer el color base para destacar selección
                    return shadeColor(baseColor, -20);
                }
            }
        },
        
        // Estilo para enlaces seleccionados
        {
            selector: 'edge:selected',
            style: {
                'line-color': '#000000',
                'width': 2.5
            }
        }
    ],
    
    layout: {
        name: 'cose',
        animate: true,
        animationDuration: 1000,
        randomize: false,
        componentSpacing: 40,
        nodeOverlap: 20,
        refresh: 20,
        fit: true,
        padding: 30,
        gravity: 0.1,
        numIter: 1000,
        initialTemp: 1000,
        coolingFactor: 0.95,
        minTemp: 1.0
    }
});

// Función auxiliar para oscurecer/aclarar un color hexadecimal
function shadeColor(color, percent) {
    let R = parseInt(color.substring(1,3), 16);
    let G = parseInt(color.substring(3,5), 16);
    let B = parseInt(color.substring(5,7), 16);

    R = parseInt(R * (100 + percent) / 100);
    G = parseInt(G * (100 + percent) / 100);
    B = parseInt(B * (100 + percent) / 100);

    R = (R<255)?R:255;
    G = (G<255)?G:255;
    B = (B<255)?B:255;

    R = Math.round(R);
    G = Math.round(G);
    B = Math.round(B);

    const RR = ((R.toString(16).length==1) ? '0'+R.toString(16) : R.toString(16));
    const GG = ((G.toString(16).length==1) ? '0'+G.toString(16) : G.toString(16));
    const BB = ((B.toString(16).length==1) ? '0'+B.toString(16) : B.toString(16));

    return '#' + RR + GG + BB;
}

// Función para cargar el grafo desde el endpoint de la API
document.addEventListener('DOMContentLoaded', function() {
    fetch('/api/graph?format=compact')
        .then(response => response.json())
        .then(data => {
            const nodes = data.nodes || [];
            const edges = data.links || [];
            
            // Asignar comunidad a cada nodo si no existe
            nodes.forEach(node => {
                if (!node.data.hasOwnProperty('community') && !node.data.hasOwnProperty('cluster_id')) {
                    node.data.community = 0; // Comunidad por defecto
                }
            });
            
            // Cargar nodos y enlaces en Cytoscape
            cy.add({
                nodes: nodes.map(n => ({
                    data: n.data,
                    classes: 'node'
                })),
                edges: edges.map(e => ({
                    data: {
                        id: e.id || `${e.source}_${e.target}`,
                        source: e.source,
                        target: e.target,
                        weight: e.weight || 1
                    },
                    classes: 'edge'
                }))
            });
            
            // Ajustar el layout después de cargar los datos
            cy.layout({
                name: 'cose',
                animate: true,
                animationDuration: 1000,
                fit: true
            }).run();
        })
        .catch(error => {
            console.error('Error al cargar el grafo:', error);
            document.getElementById('cy').innerHTML = '<div class="error">⚠️ Error al cargar el grafo. Intenta recargar la página.</div>';
        });
});

// ============================================================================
// Exportar funciones para depuración (opcional)
// ============================================================================
if (typeof window !== 'undefined') {
    window.getNodeColor = getNodeColor;
    window.shadeColor = shadeColor;
}