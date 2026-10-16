# Put this app online using only your web browser (no installs, no terminal)

You need: a GitHub account and this folder unzipped on your laptop.

## Part A - Put the files on GitHub (about 5 minutes)

1. Go to github.com, click **+** (top right) -> **New repository**.
2. Name it `freight-docs`. Choose **Private**. Click **Create repository**.
3. On the new page click **uploading an existing file**.
4. Open the unzipped `freight_app` folder, select **everything inside it** (including the `.streamlit` and
   `.devcontainer` folders - if you can't see them, turn on "Show hidden items" in File Explorer > View)
   and drag it all into the GitHub page.
5. Wait for the upload, then click **Commit changes**.

Check that `app.py`, `core.py`, `requirements.txt` and `packages.txt` appear at the top level of the repo
(not inside another folder).

## Part B - Run it from GitHub (pick one)

### Option 1: Streamlit Community Cloud (permanent link, easiest for colleagues)

1. Go to **share.streamlit.io** and sign in with GitHub.
2. Click **Create app** -> **Deploy a public app from GitHub** (or "Yup, I have an app").
3. Repository: `your-name/freight-docs`, Branch: `main`, Main file path: `app.py`.
4. Open **Advanced settings** and choose **Python 3.12**.
5. Click **Deploy**. The first start takes several minutes (it installs LibreOffice).
6. You get a link like `https://something.streamlit.app`. Share that link.
7. In the app's **Settings -> Sharing**, restrict viewing to specific email addresses (your colleagues).
   If you do not see this option on your plan, **do not use real customer documents**.

### Option 2: GitHub Codespaces (no extra website; each person needs GitHub access)

1. Open your repo on GitHub, click the green **Code** button -> **Codespaces** -> **Create codespace on main**.
2. Wait a few minutes. The app opens in a browser tab automatically.
3. Codespaces are private to the person who started them and have a monthly free-hours allowance.
   Colleagues would each need to be added to the repo and start their own.

## IMPORTANT before using real shipment documents

GitHub, Streamlit Cloud and Codespaces are **outside your company's own systems**. Your brief says freight
and customer documents must only be processed in a company-approved environment. Test with dummy files,
then ask your manager or IT whether this hosting is allowed before using real documents.
