import { startServer } from "./server.js";

const PORT = Number(process.env.PORT ?? 8100);

startServer(PORT)
  .then(() => console.log(`orchestrator listening on http://127.0.0.1:${PORT}`))
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
