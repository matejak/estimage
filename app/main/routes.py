import io

import flask
import flask_login
import matplotlib

from . import bp
from .forms import LoginForm, PointEstimationForm, NumberEstimationForm
from .. import data
from .. import utilities
from .. import simpledata
from ..users import User


matplotlib.use('Agg')
import matplotlib.pyplot as plt


def render_template(template_basename, title, **kwargs):
    authenticated_user = ""
    if flask_login.current_user.is_authenticated:
        authenticated_user = flask_login.current_user
    return flask.render_template(
        template_basename, title=title, authenticated_user=authenticated_user, ** kwargs)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User(form.username.data)
        flask_login.login_user(user, remember=form.remember_me.data)
        return flask.redirect("/")
    return render_template(
        'login.html', title='Sign In', form=form)


def tell_pollster_about_obtained_data(pollster, task_id, form_data):
    est = data.EstimInput(form_data.most_likely.data)
    est.pessimistic = form_data.pessimistic.data
    est.optimistic = form_data.optimistic.data
    pollster.tell_points(task_id, est)


def ask_pollster_of_existing_data(pollster, task_id):
    task_estimates = pollster.ask_points(task_id)
    if not task_estimates:
        return None

    est = data.Estimate.from_triple(
        float(task_estimates.most_likely),
        float(task_estimates.optimistic),
        float(task_estimates.pessimistic))

    return est


def feed_estimation_to_form_and_arg_dict(estimation, form_data, arg_dict):
    form_data.optimistic.data = estimation.source.optimistic
    form_data.most_likely.data = estimation.source.most_likely
    form_data.pessimistic.data = estimation.source.pessimistic

    arg_dict["estimate"] = estimation


def retreive_task(task_id):
    try:
        ret = simpledata.Target.load_metadata(task_id)
    except RuntimeError:
        ret = simpledata.Target()
        ret.name = task_id
        ret.title = f"{task_id} - Task Title"
        ret.description = "task <strong>description</strong>"
    return ret


@flask_login.login_required
@bp.route('/estimate/<task_name>', methods=['GET', 'POST'])
def estimate(task_name):
    user = flask_login.current_user

    user_id = user.get_id()
    pollster = simpledata.Pollster(user_id)
    form = NumberEstimationForm()
    if form.validate_on_submit():
        tell_pollster_about_obtained_data(pollster, task_name, form)
        return flask.redirect(flask.url_for("main.estimate", task_name=task_name))

    t = retreive_task(task_name)
    estimation_args = dict()
    estimation = ask_pollster_of_existing_data(pollster, task_name)
    if estimation:
        feed_estimation_to_form_and_arg_dict(estimation, form, estimation_args)

    if estimation_args:
        supply_similar_tasks(user_id, task_name, estimation_args)
    return render_template(
        'issue_view.html', title='Estimate Issue',
        user=user, form=form, task=t, ** estimation_args)


@flask_login.login_required
@bp.route('/view/<epic_name>')
def view(epic_name):
    user = flask_login.current_user

    user_id = user.get_id()
    model = get_custom_model(user_id)

    t = retreive_task(epic_name)

    return render_template(
        'epic_view.html', title='View epic', epic=t, estimate=model.point_estimate_of(epic_name))


def send_figure_as_png(figure, filename):
    bytesio = io.BytesIO()
    figure.savefig(bytesio, format="png")
    bytesio.seek(0)

    return flask.send_file(bytesio, download_name=filename, mimetype="image/png")


def get_pert_in_figure(estimation, task_name):
    pert = estimation.get_pert()

    fig, ax = plt.subplots(1, 1)
    ax.plot(pert[0], pert[1], 'b-', lw=2, label=f'task {task_name}')
    ax.axvline(estimation.expected, color="orange", label="expected value")
    ax.set_xlabel("points")
    ax.set_ylabel("probability density")
    ax.set_yticklabels([])
    ax.grid()
    ax.legend()

    return fig


@flask_login.login_required
@bp.route('/vis/<task_name>-pert.png')
def visualize_task(task_name):
    user = flask_login.current_user

    user_id = user.get_id()
    model = get_custom_model(user_id)
    if task_name == ".":
        estimation = model.main_composition.point_estimate
    else:
        estimation = model.point_estimate_of(task_name)

    fig = get_pert_in_figure(estimation, task_name)

    return send_figure_as_png(fig, f'{task_name}.png')


def get_reduced_targets():
    all_targets = simpledata.Target.load_all_targets()
    reduced_targets = utilities.reduce_subsets_from_sets(all_targets)
    return reduced_targets


def get_model(reduced_targets=None):
    if not reduced_targets:
        reduced_targets = get_reduced_targets()
    main_composition = simpledata.Target.to_tree(reduced_targets)
    model = data.EstiModel()
    model.use_composition(main_composition)
    return model


def get_custom_model(user_id, reduced_targets=None):
    pollster = simpledata.Pollster(user_id)
    model = get_model(reduced_targets)
    pollster.inform_results(model.get_all_task_models())
    return model


def order_nearby_tasks(reference_task, all_tasks, distance_threshold, rank_threshold):
    reference_estimate = reference_task.point_estimate
    expected = reference_estimate.expected

    distance_task_map = dict()
    for t in all_tasks:
        if t.name == reference_task.name:
            continue
        distance = abs(t.point_estimate.expected - expected)
        rank = t.point_estimate.rank_distance(reference_estimate)
        if (distance < distance_threshold or rank < rank_threshold):
            distance_task_map[distance] = t
    distances = sorted(list(distance_task_map.keys()))
    return [distance_task_map[dst] for dst in distances]


def supply_similar_tasks(user_id, task_name, estimation_data):
    model = get_custom_model(user_id)
    all_tasks = model.get_all_task_models()
    ordered_tasks = order_nearby_tasks(model.get_element(task_name), all_tasks, 0.5, 2)
    estimation_data["similar_sized_tasks"] = ordered_tasks


@flask_login.login_required
@bp.route('/')
def tree_view():
    user = flask_login.current_user
    user_id = user.get_id()

    reduced_targets = get_reduced_targets()
    model = get_custom_model(user_id, reduced_targets)
    return render_template(
        "tree_view.html", title="Tasks tree view", targets=reduced_targets, model=model)
