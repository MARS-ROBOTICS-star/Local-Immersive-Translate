import { getPref, setPref } from "./prefs";

export async function generateId(length: number) {
  let result = "";
  const characters =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  const charactersLength = characters.length;
  let counter = 0;
  while (counter < length) {
    result += characters.charAt(Math.floor(Math.random() * charactersLength));
    counter += 1;
  }
  return result;
}

export const getInstallInfo = async () => {
  const userId = getPref("fakeUserId");
  if (userId) {
    return {
      fakeUserId: userId,
    };
  }
  const fakeUserId = await generateId(64);
  setPref("fakeUserId", fakeUserId);
  return {
    fakeUserId: fakeUserId,
  };
};
