const TARGET_HOURS_TR = new Set([6, 7, 8, 9, 11, 13, 15, 17, 19, 20]);

function turkeyHour(date = new Date()) {
  return Number(new Intl.DateTimeFormat("en-US", {
    timeZone: "Europe/Istanbul",
    hour: "numeric",
    hour12: false,
  }).format(date));
}

async function dispatchGitHub(env) {
  const response = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPOSITORY}/actions/workflows/${env.GITHUB_WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        "User-Agent": "otoXtra-cloudflare-scheduler",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: env.GITHUB_REF || "main" }),
    },
  );

  if (!response.ok) {
    throw new Error(`GitHub dispatch failed: HTTP ${response.status} ${await response.text()}`);
  }
}

async function handle(request, env, scheduled = false) {
  if (!scheduled && request.headers.get("Authorization") !== `Bearer ${env.TRIGGER_SECRET}`) {
    return new Response("Unauthorized", { status: 401 });
  }

  const hour = turkeyHour();
  if (!TARGET_HOURS_TR.has(hour)) {
    return Response.json({ ok: true, dispatched: false, reason: "outside_target_hours", turkey_hour: hour });
  }

  await dispatchGitHub(env);
  return Response.json({ ok: true, dispatched: true, turkey_hour: hour });
}

export default {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(handle(new Request("https://scheduler.internal/"), env, true));
  },

  async fetch(request, env) {
    try {
      return await handle(request, env);
    } catch (error) {
      console.error(error);
      return new Response(`Dispatch error: ${error.message}`, { status: 502 });
    }
  },
};
