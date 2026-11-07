// mock.html's entry: installs the fake core for `?scenario=…`, then starts the real interface.
import { installFakeCore } from "./ipc";

const scenario = new URLSearchParams(location.search).get("scenario") ?? "two";
installFakeCore(scenario as Parameters<typeof installFakeCore>[0]);
await import("../main");
