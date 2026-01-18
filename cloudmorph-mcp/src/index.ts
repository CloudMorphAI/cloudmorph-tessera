import express from "express";
import { buildRouter } from "./routes";
import { healthRouter } from "./health";

const controlCenterUrl = process.env.CONTROL_CENTER_API_URL;
if (!controlCenterUrl) {
  console.error("CONTROL_CENTER_API_URL is required");
  process.exit(1);
}

const app = express();
app.use(express.json({ limit: "1mb" }));
app.use(healthRouter());
app.use(buildRouter({ controlCenterUrl }));

const port = Number(process.env.PORT || 8080);
app.listen(port, () => {
  console.log(`cloudmorph-mcp listening on ${port}`);
});
