import { task } from "@trigger.dev/sdk/v3";

export const runBrowserAgent = task({
  id: "run-browser-agent",
  // Set maxDuration to 300 seconds (5 mins) since the agent needs time to navigate and type
  maxDuration: 300, 
  run: async (
    payload: { value: string; sheetUrl?: string; sheetId?: string },
    { ctx }
  ) => {
    console.log(`Starting browser-use agent with value: ${payload.value}`);

    const agentApiUrl = process.env.AGENT_API_URL || "http://127.0.0.1:8002/run";
    console.log(`Using AGENT_API_URL: ${agentApiUrl}`);

    try {
      const response = await fetch(agentApiUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          value: payload.value,
          sheet_url: payload.sheetUrl,
          sheet_id: payload.sheetId || process.env.SHEET_ID,
          email: process.env.GOOGLE_EMAIL,
          password: process.env.GOOGLE_PASSWORD,
        }),
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(
          `Agent API responded with status ${response.status}: ${errorText}`
        );
      }

      const data = await response.json();
      console.log("Agent finished successfully:", data);
      
      return {
        success: true,
        data,
      };
    } catch (error: any) {
      console.error(`Failed to run browser agent (AGENT_API_URL=${agentApiUrl}):`, error);
      throw error;
    }
  },
});
