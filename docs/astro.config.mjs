// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightOpenAPI, { openAPISidebarGroups } from 'starlight-openapi';

// https://astro.build/config
export default defineConfig({
    site: 'https://premierlytics.com',
	integrations: [
		starlight({
			title: 'Premierlytics API',
            lastUpdated: true,
            description: 'Fantasy Premier League data with a point-in-time guarantee.',
            plugins: [
                starlightOpenAPI([
                    {
                        base: 'reference',
                        schema: './src/openapi.json',
                        label: 'Endpoint reference',
                    },
                ]),
            ],
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/KenImade/fpl-data-platform' }],
			sidebar: [
                {
                    label: "Guides",
                    items: [
                    {
                        autogenerate: {
                        directory: "guides",
                        },
                    },
                    ],
                },
                {
                    label: "Data",
                    items: [
                    {
                        autogenerate: {
                        directory: "data",
                        },
                    },
                    ],
                },
            ],
		}),
	],
});
