(function () {
    const statsNode = document.getElementById('playlist-stats-data');
    if (!statsNode) {
        return;
    }

    let stats;
    try {
        stats = JSON.parse(statsNode.textContent);
    } catch (error) {
        console.warn('[playlist-stats] Failed to parse stats payload.', error);
        return;
    }

    if (!stats || typeof window.Chart === 'undefined') {
        return;
    }

    const rootSection = document.querySelector('.playlist-stats[data-has-stats]');
    if (!rootSection) {
        return;
    }

    const palette = [
        '#42a5f5',
        '#7c4dff',
        '#26c6da',
        '#ab47bc',
        '#ec407a',
        '#ff7043',
        '#66bb6a',
        '#ffee58',
    ];

    const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

    const genreCanvas = document.getElementById('genre-distribution-chart');
    if (genreCanvas && stats.genre_distribution) {
        const labels = Object.keys(stats.genre_distribution || {});
        const values = labels.map((label) => Number(stats.genre_distribution[label] || 0));

        if (labels.length && values.some((value) => value > 0)) {
            const backgroundColors = labels.map((_, index) => palette[index % palette.length]);
            const dataset = {
                data: values,
                backgroundColor: backgroundColors,
                borderWidth: 1,
                borderColor: '#0f1115',
                hoverBorderColor: '#1f242b',
                hoverBorderWidth: 1.5,
                hoverOffset: 8,
            };

            new Chart(genreCanvas, {
                type: 'doughnut',
                data: {
                    labels,
                    datasets: [dataset],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '55%',
                    layout: {
                        padding: 0,
                    },
                    plugins: {
                        legend: {
                            display: false,
                        },
                        tooltip: {
                            backgroundColor: 'rgba(15, 17, 21, 0.92)',
                            borderColor: 'rgba(255, 255, 255, 0.12)',
                            borderWidth: 1,
                            padding: 12,
                            titleFont: {
                                family: 'inherit',
                                size: 12,
                            },
                            bodyFont: {
                                family: 'inherit',
                                size: 12,
                            },
                            callbacks: {
                                label: (context) => {
                                    const label = context.label || '';
                                    const value = context.parsed || 0;
                                    return `${label}: ${value}%`;
                                },
                            },
                        },
                    },
                },
            });

            const legendItems = rootSection.querySelectorAll('.genre-legend li[data-genre]');
            legendItems.forEach((item, index) => {
                item.style.setProperty('--genre-color', backgroundColors[index % backgroundColors.length]);
            });
        }
    }

    const noveltyCanvas = document.getElementById('novelty-doughnut-chart');
    if (noveltyCanvas && typeof stats.novelty === 'number') {
        const noveltyValue = clamp(Number(stats.novelty) || 0, 0, 100);
        const familiarValue = clamp(100 - noveltyValue, 0, 100);

        new Chart(noveltyCanvas, {
            type: 'doughnut',
            data: {
                labels: ['Novel', 'Familiar'],
                datasets: [
                    {
                        data: [noveltyValue, familiarValue],
                        backgroundColor: ['#42a5f5', 'rgba(255, 255, 255, 0.12)'],
                        borderWidth: 1,
                        borderColor: '#0f1115',
                        hoverBorderColor: '#1f242b',
                        hoverOffset: 6,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '68%',
                rotation: -90,
                circumference: 360,
                plugins: {
                    legend: {
                        display: false,
                    },
                    tooltip: {
                        enabled: false,
                    },
                },
            },
        });
    }
})();
