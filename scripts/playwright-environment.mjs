export function playwrightEnvironment(source = process.env) {
  const environment = { ...source };
  delete environment.NO_COLOR;
  return environment;
}
