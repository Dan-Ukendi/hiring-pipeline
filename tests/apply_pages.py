FORM_BODY = """
<form action="/apply/submit" method="post" enctype="multipart/form-data">
  <label for="first_name">First name</label><input id="first_name" name="first_name" required maxlength="80">
  <label for="last_name">Last name</label><input id="last_name" name="last_name" required>
  <label for="email">Email</label><input id="email" type="email" name="email" required>
  <label for="phone">Phone</label><input id="phone" type="tel" name="phone">
  <label for="resume">Resume</label><input id="resume" type="file" name="resume" accept=".pdf,application/pdf,.docx" required>
  <label for="cover_letter">Cover letter</label><input id="cover_letter" type="file" name="cover_letter">
  <label for="linkedin">LinkedIn profile</label><input id="linkedin" name="linkedin">
  <input type="text" name="website_url_confirm" style="display:none" tabindex="-1" autocomplete="off">
  <button type="submit">Submit application</button>
</form>
"""

CLEAN_FORM = f"<html><body><h1>Data Engineer</h1>{FORM_BODY}</body></html>"

CUSTOM_QUESTIONS_FORM = """
<html><body><form action="https://jobs.example-employer.test/apply" method="post">
  <label for="n">Full name</label><input id="n" name="first_name" required>
  <label for="e">Email</label><input id="e" type="email" name="email" required>
  <label for="cv">CV</label><input id="cv" type="file" name="cv_upload" required>
  <label for="q1">Why do you want to join us?</label><textarea id="q1" name="q_why" required maxlength="2000"></textarea>
  <label for="q2">Favourite lunch spot</label><input id="q2" name="q_lunch" required>
  <label for="q3">Describe your ideal weekend</label><input id="q3" name="q_weekend" required>
  <label for="q4">Are you legally authorized to work in Belgium?</label>
  <select id="q4" name="q_auth" required><option value="">Select</option><option>Yes</option><option>No</option></select>
  <label><input type="checkbox" name="gdpr" required> I consent to the processing of my personal data (GDPR)</label>
  <button type="submit">Apply</button>
</form></body></html>
"""

PASSWORD_FORM = """
<html><body><form action="/session" method="post">
  <input name="username" required><input type="password" name="password" required><button>Sign in</button>
</form></body></html>
"""

RECAPTCHA_FORM = CLEAN_FORM.replace(
    "</body>", '<script src="https://www.google.com/recaptcha/api.js"></script><div class="g-recaptcha" data-sitekey="abc"></div></body>'
)

UNCERTAIN_PROTECTION = CLEAN_FORM.replace(
    "</body>", '<script>window.__config = {"captcha": "recaptcha-enterprise"};</script></body>'
)

HCAPTCHA_FORM = CLEAN_FORM.replace(
    "</body>", '<iframe src="https://newassets.hcaptcha.com/captcha/v1/frame.html"></iframe></body>'
)

JS_SHELL = "<html><body><div id='root'></div><script src='/app.js'></script>" + "<!-- padding -->" * 400 + "</body></html>"

RENDERED_FORM = CLEAN_FORM

MULTI_STEP_FORM = CLEAN_FORM.replace(
    "<button type=\"submit\">Submit application</button>",
    "<input type=\"hidden\" name=\"step\" value=\"1\"><button type=\"button\">Next</button>",
)

EMAIL_PAGE = (
    "<html><body><h1>Junior Data Analyst</h1><p>Please send your CV and motivation letter to "
    "<a href=\"mailto:jobs@smallco.be?subject=Application%20Data%20Analyst\">jobs@smallco.be</a> to apply.</p></body></html>"
)

EMAIL_TEXT_PAGE = (
    "<html><body><h1>Analyst</h1><p>To apply, send your resume to recruitment@smallco.be. We read every application.</p></body></html>"
)

CLOSED_TEXT_PAGE = "<html><body><h1>Oops</h1><p>This job is no longer available.</p></body></html>"

NO_ACCEPT_FORM = """
<html><body><form action="/go" method="post">
  <label for="a">Email</label><input id="a" type="email" name="email" required>
  <label for="b">Name</label><input id="b" name="first_name" required>
  <label for="c">Resume</label><input id="c" type="file" name="resume" required>
  <button type="submit">Apply</button>
</form></body></html>
"""

IMAGE_ONLY_ACCEPT = NO_ACCEPT_FORM.replace('name="resume" required', 'name="resume" accept="image/png,.jpg" required')

CLOUDFLARE_CHALLENGE = "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>"

GREENHOUSE_QUESTIONS = {
    "id": 8049101,
    "title": "Junior Data Engineer",
    "questions": [
        {"required": True, "label": "First Name", "fields": [{"name": "first_name", "type": "input_text", "values": []}]},
        {"required": True, "label": "Last Name", "fields": [{"name": "last_name", "type": "input_text", "values": []}]},
        {"required": True, "label": "Email", "fields": [{"name": "email", "type": "input_text", "values": []}]},
        {"required": False, "label": "Phone", "fields": [{"name": "phone", "type": "input_text", "values": []}]},
        {"required": True, "label": "Resume/CV", "fields": [{"name": "resume", "type": "input_file", "values": []}]},
        {"required": False, "label": "Cover Letter", "fields": [{"name": "cover_letter", "type": "input_file", "values": []}]},
        {"required": True, "label": "Are you legally authorized to work in Belgium?",
         "fields": [{"name": "question_100", "type": "multi_value_single_select",
                     "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}]}]},
        {"required": False, "label": "How did you hear about us?", "fields": [{"name": "question_101", "type": "input_text", "values": []}]},
    ],
    "compliance": [
        {"type": "eeoc", "description": "", "questions": [
            {"required": False, "label": "Gender", "fields": [{"name": "gender", "type": "multi_value_single_select",
                                                              "values": [{"label": "Male", "value": 1}]}]}]}
    ],
    "demographic_questions": {"header": "", "description": "", "questions": []},
    "location_questions": [],
    "data_compliance": [{"type": "gdpr", "requires_consent": True, "requires_processing_consent": True}],
}
