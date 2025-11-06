(() => {
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

    function initialize() {
        const statsNode = document.getElementById('playlist-stats-data');
        if (!statsNode) {
            return;
        }

        let stats;
        try {
            stats = JSON.parse(statsNode.textContent || '{}');
        } catch (error) {
            console.warn('[playlist-stats] Failed to parse stats payload.', error);
            return;
        }

        if (!stats || typeof stats !== 'object') {
            return;
        }

        const rootSection = document.querySelector('.playlist-stats[data-has-stats]');
        if (!rootSection) {
            return;
        }

        const hasChartJs = typeof window.Chart !== 'undefined';
        let topGenres = Array.isArray(stats.genre_top) ? stats.genre_top : [];
        const remainingGenres = Array.isArray(stats.genre_remaining) ? stats.genre_remaining : [];
        const totalGenres = topGenres.length + remainingGenres.length;
        if (!topGenres.length && stats.genre_distribution) {
            topGenres = Object.entries(stats.genre_distribution || {}).map(([genre, percentage]) => ({
                genre,
                percentage,
            }));
        }

        const genreCanvas = document.getElementById('genre-distribution-chart');
        if (hasChartJs && genreCanvas && topGenres.length) {
            const labels = topGenres.map((item) => item.genre || '');
            const values = topGenres.map((item) => Number(item.percentage || 0));
            if (labels.some(Boolean) && values.some((value) => value > 0)) {
                const colors = labels.map((_, index) => palette[index % palette.length]);
                new Chart(genreCanvas, {
                    type: 'doughnut',
                    data: {
                        labels,
                        datasets: [
                            {
                                data: values,
                                backgroundColor: colors,
                                borderWidth: 1,
                                borderColor: '#0f1115',
                                hoverBorderColor: '#1f242b',
                                hoverBorderWidth: 1.5,
                                hoverOffset: 8,
                            },
                        ],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        cutout: '55%',
                        layout: { padding: 0 },
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                backgroundColor: 'rgba(15, 17, 21, 0.92)',
                                borderColor: 'rgba(255, 255, 255, 0.12)',
                                borderWidth: 1,
                                padding: 12,
                                titleFont: { family: 'inherit', size: 12 },
                                bodyFont: { family: 'inherit', size: 12 },
                                callbacks: {
                                    label: (ctx) => {
                                        const label = ctx.label || '';
                                        const value = ctx.parsed || 0;
                                        return `${label}: ${value}%`;
                                    },
                                },
                            },
                        },
                    },
                });

                const topLegend = rootSection.querySelectorAll('[data-genre-list="top"] li[data-genre]');
                topLegend.forEach((item, index) => {
                    item.style.setProperty('--genre-color', colors[index % colors.length]);
                });

                const remainingLegend = rootSection.querySelectorAll('[data-genre-list="remaining"] li[data-genre]');
                remainingLegend.forEach((item, index) => {
                    const color = palette[(colors.length + index) % palette.length];
                    item.style.setProperty('--genre-color', color);
                });
            }
        } else {
            const combinedLegend = rootSection.querySelectorAll('.genre-legend li[data-genre]');
            combinedLegend.forEach((item, index) => {
                item.style.setProperty('--genre-color', palette[index % palette.length]);
            });
        }

        const genreToggle = rootSection.querySelector('[data-role="genre-toggle"]');
        const genreRemainingList = rootSection.querySelector('[data-genre-list="remaining"]');
        if (genreToggle && genreRemainingList && totalGenres > 3) {
            let expanded = false;
            const updateToggle = () => {
                if (expanded) {
                    genreRemainingList.classList.remove('genre-legend--hidden');
                    genreToggle.textContent = 'Show Top Genres';
                    genreToggle.setAttribute('aria-expanded', 'true');
                } else {
                    genreRemainingList.classList.add('genre-legend--hidden');
                    genreToggle.textContent = 'Show All Genres';
                    genreToggle.setAttribute('aria-expanded', 'false');
                }
            };
            genreToggle.addEventListener('click', (event) => {
                event.preventDefault();
                expanded = !expanded;
                updateToggle();
            });
            updateToggle();
        }

        const noveltyCanvas = document.getElementById('novelty-doughnut-chart');
        if (hasChartJs && noveltyCanvas && typeof stats.novelty === 'number') {
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
                        legend: { display: false },
                        tooltip: { enabled: false },
                    },
                },
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize, { once: true });
    } else {
        initialize();
    }
})();
