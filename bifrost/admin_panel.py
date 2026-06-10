# bifrost/admin_panel.py
from flask import session, redirect, url_for, request, flash
from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.pymongo import ModelView
from flask_admin.actions import action
from bson import ObjectId
from werkzeug.security import check_password_hash, generate_password_hash
from wtforms import form, fields, validators
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")


# --- Forms ---
class AdminLoginForm(form.Form):
    email = fields.StringField('Email', [validators.DataRequired()])
    password = fields.PasswordField('Password', [validators.DataRequired()])


class ApplicationForm(form.Form):
    app_name = fields.StringField('App Name', [validators.DataRequired()])
    app_callback_url = fields.StringField('Auth Callback URL', [validators.DataRequired()])
    app_web_url = fields.StringField('Application Web URL (Home Page)')
    app_api_url = fields.StringField('API Base URL (e.g. https://api.myapp.com)', [validators.Optional()])
    app_logo_url = fields.StringField('Logo URL (HTTPS)')
    allowed_auth_methods = fields.StringField('Auth Methods (csv)', default="email,telegram")
    telegram_bot_token = fields.StringField('Telegram Bot Token (Optional)')


class AccountForm(form.Form):
    email = fields.StringField('Email', [validators.DataRequired(), validators.Email()])
    display_name = fields.StringField('Display Name')
    telegram_id = fields.StringField('Telegram ID')
    is_active = fields.BooleanField('Is Active', default=True)


class AppLinkForm(form.Form):
    account_id = fields.StringField('Account ID (ObjectId)', [validators.DataRequired()])
    role = fields.SelectField('Role', choices=[
        ('user', 'User'),
        ('premium_user', 'Premium User'),
        ('admin', 'Admin'),
        ('owner', 'Owner')  # <--- Added Owner
    ])


class SuperAdminForm(form.Form):
    email = fields.StringField('Email', [validators.DataRequired(), validators.Email()])
    password = fields.PasswordField('Password')
    created_at = fields.DateTimeField('Created At')


# --- Views ---
class BifrostAdminIndexView(AdminIndexView):
    @expose('/')
    def index(self):
        if not session.get('is_admin'):
            return redirect(url_for('.login_view'))
        return super(BifrostAdminIndexView, self).index()

    @expose('/login', methods=('GET', 'POST'))
    def login_view(self):
        form = AdminLoginForm(request.form)
        if request.method == 'POST' and form.validate():
            mongo = self.admin.app.mongo_client
            db_name = self.admin.app.config.get('DB_NAME', 'bifrost_db')
            admin = mongo[db_name].admins.find_one({"email": form.email.data})

            if admin and check_password_hash(admin['password_hash'], form.password.data):
                session['is_admin'] = True
                return redirect(url_for('.index'))

            flash('Invalid Credentials', 'error')

        self._template_args['form'] = form
        return self.render('admin/login.html')

    @expose('/logout')
    def logout_view(self):
        session.pop('is_admin', None)
        return redirect(url_for('.index'))


class SecureModelView(ModelView):
    def is_accessible(self):
        return session.get('is_admin')

    def inaccessible_callback(self, name, **kwargs):
        return redirect(url_for('admin.login_view'))


class ApplicationsView(SecureModelView):
    column_list = ('app_name', 'client_id', 'app_api_url', 'created_at')
    form = ApplicationForm

    def on_model_change(self, form, model, is_created):
        if is_created:
            safe_name = form.app_name.data.lower().replace(' ', '_')
            model['client_id'] = f"{safe_name}_{secrets.token_hex(4)}"

            raw_secret = secrets.token_urlsafe(32)
            model['client_secret_hash'] = generate_password_hash(raw_secret)
            model['created_at'] = datetime.now(UTC)

            methods = form.allowed_auth_methods.data.split(',')
            model['allowed_auth_methods'] = [m.strip() for m in methods]

            flash(f"⚠️ NEW APP REGISTERED! COPY THIS SECRET NOW: {raw_secret}", "warning")

    @action('rotate_secret', 'Rotate Secret', 'Are you sure you want to rotate the secret for the selected applications? This will invalidate the existing secret immediately.')
    def action_rotate_secret(self, ids):
        try:
            for app_id in ids:
                new_secret = secrets.token_urlsafe(32)
                self.coll.update_one(
                    {"_id": ObjectId(app_id)},
                    {"$set": {"client_secret_hash": generate_password_hash(new_secret)}}
                )
                app = self.coll.find_one({"_id": ObjectId(app_id)})
                app_name = app.get('app_name', 'Unknown App')
                flash(f"⚠️ SECRET ROTATED FOR '{app_name}'! COPY NEW SECRET NOW: {new_secret}", "warning")
        except Exception as ex:
            if not self.handle_view_exception(ex):
                raise
            flash(f'Failed to rotate secrets. {str(ex)}', 'error')


class AccountsView(SecureModelView):
    column_list = ('email', 'display_name', 'telegram_id', 'is_active', 'created_at')
    form = AccountForm
    can_create = False
    can_edit = True
    can_delete = True


class AppLinksView(SecureModelView):
    column_list = ('account_id', 'app_id', 'role', 'linked_at')
    form = AppLinkForm
    can_create = False
    can_edit = True
    can_delete = True


class SuperAdminsView(SecureModelView):
    column_list = ('email', 'created_at')
    form = SuperAdminForm
    can_create = True
    can_edit = False
    can_delete = True

    def on_model_change(self, form, model, is_created):
        if is_created:
            if form.password.data:
                model['password_hash'] = generate_password_hash(form.password.data)
            model['created_at'] = datetime.now(UTC)


def init_admin(app, mongo):
    """Registers the Admin views."""
    admin = Admin(
        app,
        name='Bifrost IdP',
        index_view=BifrostAdminIndexView()
    )
    app.mongo_client = mongo.cx
    db = mongo.cx[app.config.get('DB_NAME', 'bifrost_db')]

    admin.add_view(ApplicationsView(db.applications, 'Applications'))
    admin.add_view(AccountsView(db.accounts, 'User Accounts'))
    admin.add_view(AppLinksView(db.app_links, 'App Links'))
    admin.add_view(SuperAdminsView(db.admins, 'Super Admins'))