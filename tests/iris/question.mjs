// A real Pi dialog, reported through Herdr's supported extension event.
export default function (pi) {
  pi.registerCommand("iris-test-question", {
    description: "Open the disposable integration-test question",
    handler: async (_args, ctx) => {
      pi.events.emit("herdr:blocked", { active: true, label: "Fixture question" });
      try {
        await ctx.ui.select("Fixture question", ["First", "Second"]);
      } finally {
        pi.events.emit("herdr:blocked", { active: false });
      }
    },
  });
}
