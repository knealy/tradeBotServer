#!/usr/bin/env python3
"""Standalone preview server for master_control_preview_{1..4}.html.

Runs on a fixed dev port so you can open the redesign prototypes
side-by-side with the live master GUI without restarting anything.

    python3 gui/serve_previews.py            # default port 8787
    PORT=9000 python3 gui/serve_previews.py  # custom port

Routes (mirror the routes added to gui/chart_html.py so the variant
switcher inside each prototype navigates correctly):
    /                    -> redirects to /master/preview/1
    /master/preview/{n}  -> serves master_control_preview_{n}.html
    /master              -> small banner pointing back to the live GUI
"""
from __future__ import annotations

import os
from pathlib import Path

from aiohttp import web

HERE = Path(__file__).resolve().parent
ALLOWED = {'1', '2', '3', '4', '5', '6'}


async def handle_preview(request: web.Request) -> web.Response:
    n = request.match_info.get('n', '1')
    if n not in ALLOWED:
        return web.Response(status=404, text=f'Unknown preview: {n}')
    p = HERE / f'master_control_preview_{n}.html'
    if not p.exists():
        return web.Response(status=404, text=f'Missing file: {p.name}')
    return web.Response(
        text=p.read_text(encoding='utf-8'),
        content_type='text/html',
        headers={'Cache-Control': 'no-cache', 'Access-Control-Allow-Origin': '*'},
    )


async def handle_v2(request: web.Request) -> web.Response:
    """Serve master_control_v2.html so the layout can be inspected on the preview server.

    The page wires to /api/chart/* endpoints which DO NOT exist here, so chart/data
    panels will appear empty. This is intentional — for full functionality run the
    live master GUI (python trading_bot.py --command=master) which serves the same
    HTML at /master/v2 on its own port with all endpoints wired.
    """
    p = HERE / 'master_control_v2.html'
    if not p.exists():
        return web.Response(status=404, text='master_control_v2.html missing')
    port = int(os.environ.get('PORT', '8787'))
    body = p.read_text(encoding='utf-8')
    body = body.replace('{{SERVER_PORT}}', str(port))
    body = body.replace('{{SYMBOL}}', 'MNQ')
    body = body.replace('{{TIMEFRAME}}', '5m')
    return web.Response(
        text=body,
        content_type='text/html',
        headers={'Cache-Control': 'no-cache', 'Access-Control-Allow-Origin': '*'},
    )


async def handle_index(request: web.Request) -> web.Response:
    return web.HTTPFound('/master/preview/1')


async def handle_live_redirect(request: web.Request) -> web.Response:
    return web.Response(
        text=(
            '<html><body style="font-family:system-ui;background:#15171a;color:#e9e5dd;'
            'padding:40px;line-height:1.6">'
            '<h2 style="font-weight:400">This is the preview server.</h2>'
            '<p>The live master GUI runs on its own port (run '
            '<code>python trading_bot.py --command=master</code> to start it).'
            '<br>For the prototypes, use:'
            '<ul><li><a style="color:#8aa39b" href="/master/preview/1">/master/preview/1</a> &mdash; Pure Paper</li>'
            '<li><a style="color:#8aa39b" href="/master/preview/2">/master/preview/2</a> &mdash; Bound Paper</li>'
            '<li><a style="color:#8aa39b" href="/master/preview/3">/master/preview/3</a> &mdash; Reading Room</li>'
            '<li><a style="color:#8aa39b" href="/master/preview/4">/master/preview/4</a> &mdash; Whitespace</li>'
            '<li><a style="color:#8aa39b" href="/master/preview/5">/master/preview/5</a> &mdash; Atelier Press (P3+P4 hybrid)</li>'
            '<li><a style="color:#8aa39b" href="/master/preview/6">/master/preview/6</a> &mdash; Quiet Trader (P4 with safer trade pills)</li>'
            '<li><a style="color:#8aa39b" href="/master/v2">/master/v2</a> &mdash; <b>Quiet Trader, wired</b> (P6 + live API hooks; needs the live bot for data)</li>'
            '</ul></p></body></html>'
        ),
        content_type='text/html',
    )


def main() -> None:
    port = int(os.environ.get('PORT', '8787'))
    app = web.Application()
    app.router.add_get('/', handle_index)
    app.router.add_get('/master', handle_live_redirect)
    app.router.add_get('/master/preview/{n}', handle_preview)
    app.router.add_get('/master/v2', handle_v2)
    print(f'Preview server: http://127.0.0.1:{port}/master/preview/1')
    web.run_app(app, host='127.0.0.1', port=port, print=lambda *_a, **_k: None)


if __name__ == '__main__':
    main()
